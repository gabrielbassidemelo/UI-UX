"""Cópia externa dos backups (já cifrados, HMBK2) para o Oracle Object Storage via PAR de bucket.

A URL da PAR é uma CREDENCIAL: fica só no segredo OCI_PAR_URL, nunca em log, exceção, estado ou commit.
Somente HTTPS e host terminado em ".oraclecloud.com". Uma PAR NÃO pode excluir objetos (documentação
da Oracle): a retenção remota deve ser feita por regra de ciclo de vida e versionamento do bucket.

Os parâmetros `_inseguro_para_testes` existem só para os testes com servidor HTTP falso local; o
código de produção nunca os usa e não há variável de ambiente que os ative.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from .backup import latest_backup
from .config import Config, write_private_file

log = logging.getLogger("healthmon.offsite")

STATE_FILE = "offsite_state.json"
TIMEOUT = (10, 120)               # (conexão, leitura) em segundos
MAX_ESTADO_ENVIADOS = 200
ATRASO_HORAS = 36                  # RPO de 24 h + folga; acima disso o monitoramento acusa atraso
_TOKEN_RE = re.compile(r"/p/[^/\s\"']+")


class OffsiteError(RuntimeError):
    """Mensagens nunca contêm a URL da PAR."""


class _RedactFilter(logging.Filter):
    """O urllib3 registra o caminho da requisição em DEBUG; ele contém o token da PAR."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "/p/" in msg:
            record.msg, record.args = _TOKEN_RE.sub("/p/***", msg), ()
        return True


for _name in ("urllib3.connectionpool", "urllib3", "requests"):
    logging.getLogger(_name).addFilter(_RedactFilter())


# --- URL da PAR ----------------------------------------------------------------------
def _base_url(cfg: Config, par_url: str | None, inseguro: bool) -> str:
    url = (par_url or cfg.secret("OCI_PAR_URL") or "").strip()
    if not url:
        raise OffsiteError("OCI_PAR_URL não configurada em secrets.env")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not inseguro:
        if parts.scheme != "https":
            raise OffsiteError("OCI_PAR_URL inválida: somente HTTPS")
        if not host.endswith(".oraclecloud.com"):
            raise OffsiteError("OCI_PAR_URL inválida: o host deve terminar em .oraclecloud.com")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise OffsiteError("OCI_PAR_URL inválida: formato inesperado")
    path = parts.path
    if not path.endswith("/"):
        path += "/"
    if "/p/" not in path or not path.endswith("/o/"):
        raise OffsiteError("OCI_PAR_URL inválida: esperado .../p/<token>/n/<namespace>/b/<bucket>/o/")
    return f"{parts.scheme}://{parts.netloc}{path}"


def configured(cfg: Config) -> bool:
    return bool(cfg.secret("OCI_PAR_URL"))


def _prefix(cfg: Config) -> str:
    p = str(cfg.get("backup", "offsite_prefixo", "") or "")
    return p if not p or p.endswith("/") else p + "/"


def _request(method: str, url: str, *, tentativas: int, espera_base: float, **kw) -> requests.Response:
    """Requisição com timeout e novas tentativas com espera crescente (5xx, 429 e erros de rede).
    Qualquer erro é reescrito sem a URL."""
    ultimo = "falha desconhecida"
    for i in range(max(1, tentativas)):
        if i:
            time.sleep(espera_base * (2 ** (i - 1)))
        try:
            r = requests.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kw)
        except requests.RequestException as e:
            ultimo = f"falha de rede ({type(e).__name__})"
            _rewind(kw)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            ultimo = f"servidor respondeu HTTP {r.status_code}"
            r.close()
            _rewind(kw)
            continue
        if r.status_code >= 300:
            code = r.status_code
            r.close()
            dica = " (PAR expirada, revogada ou sem permissão)" if code in (401, 403, 404) else ""
            raise OffsiteError(f"HTTP {code}{dica}")
        return r
    raise OffsiteError(f"{ultimo} após {max(1, tentativas)} tentativas")


def _rewind(kw: dict) -> None:
    d = kw.get("data")
    if hasattr(d, "seek"):
        d.seek(0)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- operações -------------------------------------------------------------------------
def upload(cfg: Config, path: Path, *, par_url: str | None = None, tentativas: int = 4,
           espera_base: float = 2.0, _inseguro_para_testes: bool = False) -> str:
    """PUT do arquivo cifrado. Devolve o nome do objeto remoto (nome do arquivo, com data/hora)."""
    base = _base_url(cfg, par_url, _inseguro_para_testes)
    path = Path(path)
    name = _prefix(cfg) + path.name
    with open(path, "rb") as f:
        _request("PUT", base + quote(name), data=f, tentativas=tentativas, espera_base=espera_base,
                 headers={"Content-Type": "application/octet-stream"}).close()
    return name


NAME_RE = re.compile(r"^healthmon-(\d{8})-(\d{6})\.hmbk$")
MAX_DOWNLOAD = 512 * 1024 * 1024


def _safe_name(name: str, prefix: str) -> str:
    """Só aceita <prefixo>healthmon-AAAAMMDD-HHMMSS.hmbk; nada de barras extras, '..' ou outros nomes."""
    base = name[len(prefix):] if prefix and name.startswith(prefix) else name
    if not NAME_RE.match(base) or name != prefix + base:
        raise OffsiteError("nome de objeto remoto fora do padrão esperado; ignorado por segurança")
    return base


def list_remote(cfg: Config, *, par_url: str | None = None, tentativas: int = 4, espera_base: float = 2.0,
                _inseguro_para_testes: bool = False) -> list[dict]:
    """Lista objetos (exige PAR com listagem habilitada). Segue `nextStartWith` (ListObjects)."""
    base = _base_url(cfg, par_url, _inseguro_para_testes)
    out: list[dict] = []
    start = None
    for _ in range(1000):
        params = {"fields": "name,size", "prefix": _prefix(cfg)} if _prefix(cfg) else {"fields": "name,size"}
        if start:
            params["start"] = start
        r = _request("GET", base, params=params, tentativas=tentativas, espera_base=espera_base)
        try:
            doc = r.json()
        except ValueError:
            raise OffsiteError("resposta de listagem inválida (não é JSON)") from None
        finally:
            r.close()
        out += [{"name": o["name"], "size": o.get("size")} for o in doc.get("objects", []) if "name" in o]
        start = doc.get("nextStartWith")
        if not start:
            break
    return sorted(out, key=lambda o: o["name"])


def download(cfg: Config, name: str, dest: Path, *, par_url: str | None = None, tentativas: int = 4,
             espera_base: float = 2.0, _inseguro_para_testes: bool = False) -> Path:
    """GET do objeto para `dest` (gravação atômica, permissão 600)."""
    base = _base_url(cfg, par_url, _inseguro_para_testes)
    _safe_name(name, _prefix(cfg))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _request("GET", base + quote(name, safe="/"), stream=True, tentativas=tentativas, espera_base=espera_base)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".dl-")
    done = False
    try:
        got = 0
        with os.fdopen(fd, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                got += len(chunk)
                if got > MAX_DOWNLOAD:
                    raise OffsiteError("objeto remoto maior que o limite aceito; download interrompido")
                f.write(chunk)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
        done = True
    except requests.RequestException as e:
        raise OffsiteError(f"falha de rede no download ({type(e).__name__})") from None
    finally:
        r.close()
        if not done:
            Path(tmp).unlink(missing_ok=True)
    return dest


def verify_remote(cfg: Config, path: Path, *, par_url: str | None = None, tentativas: int = 4,
                  espera_base: float = 2.0, _inseguro_para_testes: bool = False) -> bool:
    """Baixa o objeto e compara o SHA-256 do arquivo cifrado com o do arquivo local."""
    path = Path(path)
    with tempfile.TemporaryDirectory() as td:
        got = download(cfg, _prefix(cfg) + path.name, Path(td) / "remoto", par_url=par_url, tentativas=tentativas,
                       espera_base=espera_base, _inseguro_para_testes=_inseguro_para_testes)
        return _sha256_file(got) == _sha256_file(path)


def list_backups(cfg: Config, **kw) -> list[dict]:
    """Backups remotos com nome no padrão, do mais recente para o mais antigo. Objetos com outros nomes são ignorados."""
    pre = _prefix(cfg)
    out = []
    for o in list_remote(cfg, **kw):
        try:
            base = _safe_name(o["name"], pre)
        except OffsiteError:
            continue
        m = NAME_RE.match(base)
        out.append({"objeto": o["name"], "arquivo": base, "tamanho": o.get("size"),
                    "data_no_nome": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}"})
    return sorted(out, key=lambda x: x["arquivo"], reverse=True)


def download_backup(cfg: Config, objeto: str, dest_dir: Path, **kw) -> Path:
    return download(cfg, objeto, Path(dest_dir) / objeto.rsplit("/", 1)[-1], **kw)


def download_latest(cfg: Config, dest_dir: Path, **kw) -> Path:
    """Baixa o backup remoto mais recente (por nome/data) para dest_dir. Quem restaura deve conferir o cabeçalho
    (data de criação e destinatário) antes de confiar: ver `healthmon restaurar --remoto`."""
    itens = list_backups(cfg, **kw)
    if not itens:
        raise OffsiteError("nenhum backup encontrado no armazenamento externo")
    return download_backup(cfg, itens[0]["objeto"], dest_dir, **kw)


# --- estado e monitoramento ------------------------------------------------------------
def _state_path(cfg: Config) -> Path:
    return cfg.home / STATE_FILE


def _load_state(cfg: Config) -> dict:
    try:
        s = json.loads(_state_path(cfg).read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(cfg: Config, s: dict) -> None:
    s["enviados"] = s.get("enviados", [])[-MAX_ESTADO_ENVIADOS:]
    write_private_file(_state_path(cfg), json.dumps(s, ensure_ascii=False, indent=1))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync_latest(cfg: Config, *, par_url: str | None = None, tentativas: int = 4, espera_base: float = 2.0,
                _inseguro_para_testes: bool = False) -> dict:
    """Envia o backup local mais recente ainda não enviado, verifica o envio e grava o estado.
    Nunca levanta por falha de rede e nunca apaga backup local. status: ok | em_dia | erro | desativado | sem_backup."""
    if not (par_url or configured(cfg)):
        return {"status": "desativado", "detalhe": "OCI_PAR_URL não configurada"}
    st = _load_state(cfg)
    p = latest_backup(cfg)
    if p is None:
        return {"status": "sem_backup"}
    if p.name in st.get("enviados", []):
        return {"status": "em_dia", "arquivo": p.name}
    try:
        kw = dict(par_url=par_url, tentativas=tentativas, espera_base=espera_base,
                  _inseguro_para_testes=_inseguro_para_testes)
        name = upload(cfg, p, **kw)
        if not verify_remote(cfg, p, **kw):
            raise OffsiteError("verificação remota falhou: SHA-256 do objeto enviado difere do local")
    except OffsiteError as e:
        st["ultimo_erro"] = {"quando": _now(), "mensagem": str(e), "arquivo": p.name}
        st["falhas_consecutivas"] = int(st.get("falhas_consecutivas", 0)) + 1
        _save_state(cfg, st)
        log.warning("envio externo falhou (%s); backup local mantido: %s", e, p.name)
        return {"status": "erro", "erro": str(e), "arquivo": p.name, "falhas_consecutivas": st["falhas_consecutivas"]}
    st.update(ultimo_envio_ok=_now(), ultimo_arquivo=p.name, ultimo_objeto=name, falhas_consecutivas=0)
    st["enviados"] = st.get("enviados", []) + [p.name]
    _save_state(cfg, st)
    return {"status": "ok", "arquivo": p.name, "objeto": name, "verificado": True}


def status(cfg: Config) -> dict:
    """Para monitoramento; não usa a rede. `alerta` é True se houver falhas ou envio atrasado."""
    st = _load_state(cfg)
    on = configured(cfg)
    ok_at = st.get("ultimo_envio_ok")
    idade = None
    if ok_at:
        idade = round((datetime.now(timezone.utc) - datetime.strptime(ok_at, "%Y-%m-%dT%H:%M:%SZ")
                       .replace(tzinfo=timezone.utc)).total_seconds() / 3600, 1)
    falhas = int(st.get("falhas_consecutivas", 0))
    ultimo_local = latest_backup(cfg)
    atrasado = on and (idade is None or idade > ATRASO_HORAS)
    return {"configurado": on, "ultimo_envio_ok": ok_at, "ultimo_arquivo": st.get("ultimo_arquivo"),
            "idade_horas": idade, "ultimo_erro": st.get("ultimo_erro"), "falhas_consecutivas": falhas,
            "pendente": bool(ultimo_local and ultimo_local.name not in st.get("enviados", [])),
            "atrasado": bool(atrasado), "alerta": bool(on and (falhas >= 2 or atrasado))}
