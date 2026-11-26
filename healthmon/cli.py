"""Linha de comando do healthmon (`healthmon --help`)."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import init_home, load_config


def _setup_logging(cfg, verbose: bool) -> None:
    cfg.home.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        import os
        logf = cfg.home / "healthmon.log"
        os.close(os.open(logf, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600))
        logf.chmod(0o600)
        handlers.append(logging.FileHandler(logf, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # A biblioteca garminconnect pode logar URLs com identificadores em DEBUG: manter em WARNING.
    logging.getLogger("garminconnect").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="healthmon", description="Monitoramento pessoal de saúde (Garmin) — local e privado.")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="cria $HEALTHMON_HOME com config.toml e secrets.env")
    lg = sub.add_parser("login", help="login único no Garmin Connect (senha não é armazenada)")
    lg.add_argument("--email")
    sy = sub.add_parser("sync", help="sincroniza os últimos dias")
    sy.add_argument("--dias", type=int, default=None)
    bf = sub.add_parser("historico", help="importa histórico (ex.: --dias 365)")
    bf.add_argument("--dias", type=int, default=90)
    sub.add_parser("reprocessar", help="recalcula métricas a partir dos JSON brutos salvos")
    rp = sub.add_parser("relatorio", help="gera um relatório agora")
    rp.add_argument("tipo", choices=["diario", "noturno", "semanal", "mensal"])
    rp.add_argument("--data", help="AAAA-MM-DD (mensal: qualquer dia do mês)")
    rp.add_argument("--notificar", action="store_true")
    sub.add_parser("disponibilidade", help="lista quais indicadores seu dispositivo está fornecendo")
    sub.add_parser("status", help="estado geral do sistema")
    sub.add_parser("senha-dashboard", help="define a senha de acesso ao dashboard")
    sv = sub.add_parser("dashboard", help="inicia só o dashboard")
    sv.add_argument("--host")
    sv.add_argument("--porta", type=int)
    sub.add_parser("executar", help="serviço completo: agendador + dashboard (uso em produção)")
    bk = sub.add_parser("backup", help="cria backup criptografado")
    bk.add_argument("--destino")
    rs = sub.add_parser("restaurar", help="restaura um backup .hmbk (sem arquivo: baixa o mais recente da cópia externa)")
    rs.add_argument("arquivo", nargs="?")
    ck = sub.add_parser("chaves-recuperacao", help="gera o par de chaves do backup; a chave de recuperação aparece UMA vez")
    ck.add_argument("--forcar", action="store_true", help="substitui a chave pública existente (backups antigos seguem exigindo a chave antiga)")
    be = sub.add_parser("backup-externo", help="envia o último backup para a cópia externa, ou mostra o estado")
    be.add_argument("--status", action="store_true")
    be.add_argument("--contar", action="store_true", help="imprime quantos backups existem na cópia externa")
    sub.add_parser("trocar-chave-garmin", help="troca a chave pessoal da Garmin (digitação oculta) e a data de expiração")
    ds = sub.add_parser("definir-segredo", help="grava um segredo em secrets.env com digitação oculta (ex.: OCI_PAR_URL)")
    ds.add_argument("nome")
    dc = sub.add_parser("definir-config", help="ajusta um valor não secreto do config.toml (uso do script de instalação)")
    dc.add_argument("secao"); dc.add_argument("chave"); dc.add_argument("valor")
    vb = sub.add_parser("verificar-backup", help="confere a integridade de um backup (o mais recente, se omitido)")
    vb.add_argument("arquivo", nargs="?")
    vb.add_argument("--com-chave", action="store_true", help="pede a chave de recuperação e decifra para conferir o banco")
    dm = sub.add_parser("demo", help="popula um banco com dados SIMULADOS (use HEALTHMON_HOME separado)")
    dm.add_argument("--dias", type=int, default=120)
    ia = sub.add_parser("ia-nuvem", help="consentimento para IA em nuvem")
    ia.add_argument("acao", choices=["previa", "consentir", "revogar", "status"])
    sub.add_parser("testar-notificacao", help="envia mensagem de teste pelos canais configurados")
    sub.add_parser("validar-garmin", help="valida a API oficial: autenticação, renovação, estrutura, unidades, fuso, histórico (sem valores)")
    sub.add_parser("conferir-garmin", help="mostra os valores de ontem para você comparar com o app Garmin Connect")
    kbp = sub.add_parser("kb", help="base científica: verificar referências no Crossref ou buscar")
    kbp.add_argument("acao", choices=["verificar", "buscar", "listar"])
    kbp.add_argument("termo", nargs="?")
    al = sub.add_parser("alimentos", help="base de alimentos: baixar-taco | importar ARQUIVO --fonte NOME")
    al.add_argument("acao", choices=["baixar-taco", "importar", "contar"])
    al.add_argument("arquivo", nargs="?")
    al.add_argument("--fonte", default="CSV")
    al.add_argument("--url")
    ex = sub.add_parser("exames", help="importa exames de um CSV (data,analito,valor,unidade,ref_min,ref_max,laboratorio)")
    ex.add_argument("arquivo")
    sub.add_parser("plano", help="mostra o plano do dia")
    xp = sub.add_parser("exportar", help="exporta TODOS os seus dados para um arquivo JSON (portabilidade)")
    xp.add_argument("destino")
    dl = sub.add_parser("apagar-dados", help="apaga definitivamente banco, tokens, relatórios, exames e backups")
    dl.add_argument("--confirmar", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        home = init_home()
        print(f"Diretório criado: {home}\n- Edite {home/'config.toml'}\n- Preencha segredos em {home/'secrets.env'} (permissão 600)\n"
              "- Depois: healthmon senha-dashboard, healthmon trocar-chave-garmin, healthmon validar-garmin e healthmon historico --dias 90\n"
              "- Em servidor, use o roteiro guiado: deploy/oracle/configurar.sh (docs/IMPLANTACAO.md)")
        return 0

    cfg = load_config()
    _setup_logging(cfg, args.verbose)
    from .db import Database
    db = Database(cfg.db_path)

    if args.cmd == "login":
        if cfg.get("garmin", "fonte", "oficial") != "nao_oficial":
            print("A fonte padrão é a API oficial da Garmin, que não usa login com senha.\n"
                  "Gere a chave em https://connect.garmin.com/app/settings/personalApiToken e grave em\n"
                  f"{cfg.home / 'secrets.env'} como GARMIN_PERSONAL_API_KEY=...  Depois: healthmon validar-garmin")
            return 1
        from .garmin import GarminConnectSource
        GarminConnectSource(cfg).interactive_login(args.email)
        print(f"Login concluído. Tokens salvos em {cfg.token_dir} (permissão 600). A senha não foi armazenada.")
        return 0

    if args.cmd in ("sync", "historico"):
        from .service import Service
        from .sync import recent_days
        n = args.dias
        svc = Service(cfg, db)
        days = recent_days(cfg, n) if n else None
        if args.cmd == "historico" and hasattr(svc.source, "fetch_range"):
            from .sync import sync_range, today_local
            end = today_local(cfg)
            res = sync_range(cfg, db, svc.source, (end - timedelta(days=n - 1)).isoformat(), end.isoformat())
            print(json.dumps(res, ensure_ascii=False), "— histórico importado por intervalo (pode ser repetido sem duplicar).")
            return 0 if res["status"] != "erro" else 1
        if args.cmd == "historico":
            days = svc.pending_days(n)
            print(f"Importando {len(days)} de {n} dias (os demais já estão gravados).")
        res = svc.sync(days)
        print(json.dumps({k: v for k, v in res.items() if k != "dias"}, ensure_ascii=False),
              f"— {len(res.get('dias', {}))} dia(s) processado(s)")
        return 0 if res["status"] != "erro" else 1

    if args.cmd == "reprocessar":
        from .sync import process_day
        days = db.raw_days()
        for d in days:
            process_day(db, d)
        print(f"{len(days)} dia(s) reprocessado(s).")
        return 0

    if args.cmd == "relatorio":
        from . import notify, reports
        from .sync import today_local
        d = date.fromisoformat(args.data) if args.data else today_local(cfg)
        if args.tipo == "diario":
            rep = reports.daily(cfg, db, d)
        elif args.tipo == "noturno":
            rep = reports.nightly(cfg, db, d)
        elif args.tipo == "semanal":
            rep = reports.weekly(cfg, db, d)
        else:
            if not args.data:
                d = d.replace(day=1) - timedelta(days=1)
            rep = reports.monthly(cfg, db, d.year, d.month)
        print(rep.doc.markdown())
        if args.notificar:
            print("Enviado por:", notify.notify_report(cfg, args.tipo, rep.doc.title, rep.summary))
        return 0

    if args.cmd == "disponibilidade":
        from .sync import availability, today_local
        av = availability(db, 30, today_local(cfg))
        for st in ("disponível", "parcial", "indisponível", "sem dados"):
            items = [v for v in av.values() if v["status"] == st]
            if items:
                print(f"\n{st.upper()}:")
                for v in items:
                    print(f"  - {v['rotulo']}: {v['dias_com_dado']}/{v['dias']} dias")
        return 0

    if args.cmd == "status":
        from .llm import consent_status
        first, last = db.first_last_day()
        print(f"Banco: {cfg.db_path}\nPeríodo: {first} a {last}\nÚltima sincronização OK: {db.last_successful_sync()}")
        print(f"Fonte: {db.get_setting('data_source', 'garmin')}\nIA: {cfg.get('ia', 'modo')} "
              f"(consentimento nuvem: {'sim' if consent_status(cfg) else 'não'})")
        print(f"Notificações: {cfg.get('notificacoes', 'canais')} nível {cfg.get('notificacoes', 'nivel_conteudo')}")
        for h in db.sync_history(5):
            print(f"  {h['started_at']} {h['status']} {h['detail'][:120] if h['detail'] else ''}")
        return 0

    if args.cmd == "senha-dashboard":
        from .auth import set_password
        p1 = getpass.getpass("Nova senha do dashboard (mín. 10 caracteres): ")
        if p1 != getpass.getpass("Repita: "):
            print("As senhas não conferem.")
            return 1
        set_password(cfg, p1)
        print("Senha definida (armazenada apenas como hash scrypt).")
        return 0

    if args.cmd in ("dashboard", "executar"):
        import uvicorn
        from .web.app import create_app
        import os
        host = getattr(args, "host", None) or os.environ.get("HEALTHMON_HOST") or cfg.get("dashboard", "host", "127.0.0.1")
        port = getattr(args, "porta", None) or int(cfg.get("dashboard", "porta", 8765))
        sync_fn = None
        sched = None
        if args.cmd == "executar":
            from apscheduler.schedulers.background import BackgroundScheduler
            from .service import Service
            sched = BackgroundScheduler(timezone=cfg.tz)
            service = Service(cfg, db, scheduler=sched)
            service.schedule(sched)
            sched.start()
            sync_fn = service.sync
            import threading
            threading.Thread(target=service.catch_up, daemon=True).start()
            logging.info("Agendador iniciado: %s", ", ".join(j.id for j in sched.get_jobs()))
        app = create_app(cfg, db, sync_fn)
        beat = cfg.home / "servico.ativo"
        if sched:
            beat.touch()
            sched.add_job(beat.touch, "interval", seconds=60, id="heartbeat")     # 'restaurar' recusa com o serviço ativo
        try:
            uvicorn.run(app, host=host, port=port, server_header=False, date_header=False,
                        proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_level="warning")
        finally:
            if sched:
                sched.shutdown(wait=False)
                beat.unlink(missing_ok=True)
        return 0

    if args.cmd == "backup":
        from .backup import create_backup
        p = create_backup(cfg, Path(args.destino) if args.destino else None)
        print(f"Backup criptografado: {p}")
        return 0

    if args.cmd == "restaurar":
        import tempfile
        from . import backup as bkp, offsite
        if bkp.service_active(cfg):
            print("O serviço está em execução. Pare-o antes (docker compose stop healthmon) e repita.")
            return 1
        if args.arquivo:
            path = Path(args.arquivo)
        else:
            itens = offsite.list_backups(cfg)
            if not itens:
                print("Nenhum backup encontrado na cópia externa.")
                return 1
            print("Backups na cópia externa (mais recente primeiro):")
            for i, it in enumerate(itens[:15], 1):
                print(f"  {i:2d}. {it['data_no_nome']}  {it['tamanho'] or '?'} bytes")
            esc = input("Número do backup a restaurar [1]: ").strip() or "1"
            if not esc.isdigit() or not 1 <= int(esc) <= min(15, len(itens)):
                print("Escolha inválida.")
                return 1
            path = offsite.download_backup(cfg, itens[int(esc) - 1]["objeto"], Path(tempfile.mkdtemp(prefix="hm-restaurar-")))
        head = bkp.read_header(path)
        # A chave de recuperação só é lida do teclado: nunca por argumento, variável de ambiente ou arquivo.
        key = getpass.getpass("Chave de recuperação (HMRK-...): ").strip() if head else None
        if head:
            info = bkp.verify_backup(cfg, path, key)
            print(f"Este backup foi criado em {head.get('criado_utc')} (UTC) e contém {info['dias']} dia(s) de indicadores.")
            stamp = bkp.header_matches_name(path, head)
            if stamp is not True:
                print(f"ATENÇÃO: {stamp} Isso pode indicar um arquivo antigo copiado com outro nome.")
            if info["dias"] == 0:
                print("ATENÇÃO: este backup está VAZIO (nenhum dia de indicadores).")
            if input("Restaurar este backup sobre o banco atual? Digite RESTAURAR: ").strip() != "RESTAURAR":
                print("Nada foi alterado.")
                return 1
        print(f"Banco restaurado em {bkp.restore_backup(cfg, path, key)} (cópia anterior preservada).")
        if key and not bkp.public_key_path(cfg).exists():
            bkp.save_public_key(cfg, bkp.public_from_private(key))      # servidor reconstruído: mesma chave de recuperação
            print("Chave pública de backup regravada a partir da sua chave de recuperação: os próximos backups usam a mesma chave.")
        return 0

    if args.cmd == "chaves-recuperacao":
        from . import backup as bkp
        if bkp.public_key_path(cfg).exists() and not args.forcar:
            print("Já existe uma chave pública de backup. Use --forcar só se perdeu a chave de recuperação;\n"
                  "backups antigos continuarão exigindo a chave antiga.")
            return 1
        priv, pub = bkp.generate_recovery_keypair()
        print("CHAVE DE RECUPERAÇÃO (aparece só agora; não fica gravada no servidor):\n")
        print(f"    {priv}\n")
        print("Guarde no gerenciador de senhas do iPhone (Senhas/iCloud) ou em papel. Sem ela NENHUM backup novo pode ser\n"
              "aberto, nem por você, nem pelo servidor. Não a envie por conversa, e-mail ou GitHub.")
        input("Depois de guardar, pressione Enter para limpar a tela. ")
        print("\033[2J\033[3J\033[H", end="")
        # Prova de que a chave foi guardada: só depois disso a chave pública passa a valer.
        for tentativa in range(3):
            typed = getpass.getpass("Cole a chave que você guardou, para conferir (não aparece na tela): ").strip()
            try:
                if bkp.public_from_private(typed) == pub:
                    break
            except bkp.BackupError:
                pass
            print("Não confere com a chave gerada.")
        else:
            print("A chave não foi confirmada. Nada foi gravado; rode o comando de novo para gerar outra chave.")
            return 1
        del priv, typed
        bkp.save_public_key(cfg, pub)
        print(f"Chave confirmada e chave pública gravada (impressão digital {bkp.expected_fingerprint(cfg)}).")
        print("Os próximos backups usam o formato HMBK2 e só abrem com a sua chave de recuperação.")
        return 0

    if args.cmd == "trocar-chave-garmin":
        from .config import set_config_value, set_secret
        k = getpass.getpass("Nova chave pessoal da Garmin (não aparece na tela): ").strip()
        if len(k) < 16:
            print("Chave vazia ou curta demais. Nada foi alterado.")
            return 1
        exp = input("Data de expiração da nova chave (AAAA-MM-DD): ").strip()
        try:
            date.fromisoformat(exp)
        except ValueError:
            print("Data inválida. Nada foi alterado.")
            return 1
        set_secret(cfg.home, "GARMIN_PERSONAL_API_KEY", k)
        set_config_value(cfg.home, "garmin", "chave_expira_em", exp)
        del k
        print("Chave trocada. Reinicie o serviço (docker compose restart healthmon) e rode 'healthmon validar-garmin'.")
        return 0

    if args.cmd == "definir-config":
        from .config import set_config_value
        if (args.secao, args.chave) not in (("dashboard", "https"), ("notificacoes", "url_dashboard"), ("garmin", "chave_expira_em")):
            print("Ajuste não permitido por este comando; edite config.toml.")
            return 1
        set_config_value(cfg.home, args.secao, args.chave, args.valor)
        return 0

    if args.cmd == "definir-segredo":
        from .config import set_secret
        if args.nome not in ("OCI_PAR_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_PASSWORD"):
            print("Segredo não reconhecido.")
            return 1
        v = getpass.getpass(f"Valor de {args.nome} (não aparece na tela): ").strip()
        if not v:
            print("Valor vazio. Nada foi alterado.")
            return 1
        set_secret(cfg.home, args.nome, v)
        print(f"{args.nome} gravado em secrets.env.")
        return 0

    if args.cmd == "backup-externo":
        from . import offsite
        if args.contar:
            try:
                print(len(offsite.list_backups(cfg)))
            except offsite.OffsiteError as e:
                print(f"Não foi possível listar a cópia externa: {e}", file=sys.stderr)
                return 1
            return 0
        r = offsite.status(cfg) if args.status else offsite.sync_latest(cfg)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if args.status or r.get("status") in ("ok", "em_dia") else 1

    if args.cmd == "verificar-backup":
        from . import backup as bkp
        path = Path(args.arquivo) if args.arquivo else bkp.latest_backup(cfg)
        if path is None:
            print("Nenhum backup encontrado.")
            return 1
        key = getpass.getpass("Chave de recuperação (HMRK-...): ").strip() if args.com_chave else None
        print(json.dumps(bkp.verify_backup(cfg, path, key), ensure_ascii=False, indent=1, default=str))
        return 0

    if args.cmd == "demo":
        from .demo import populate, populate_phase2
        populate(db, args.dias)
        populate_phase2(db, cfg, min(args.dias, 60))
        print(f"{args.dias} dias SIMULADOS gravados em {cfg.db_path}.")
        return 0

    if args.cmd == "ia-nuvem":
        from . import llm, reports
        from .sync import today_local
        if args.acao == "previa":
            day = today_local(cfg)
            from .analysis import day_snapshot
            snap = day_snapshot(db, day)
            from . import reports
            print("EXATAMENTE este conteúdo seria enviado à Anthropic (um bloco por tipo de relatório):")
            reports.PREVIEW_ONLY = True
            for kind, build in (("diário", lambda: reports.daily(cfg, db, day)),
                                ("noturno", lambda: reports.nightly(cfg, db, day)),
                                ("semanal", lambda: reports.weekly(cfg, db, day))):
                try:
                    facts = llm.strip_identifiers(build().facts)
                except Exception as e:  # noqa: BLE001
                    facts = {"indisponivel": type(e).__name__}
                print(f"\n--- relatório {kind} ---")
                print(json.dumps(facts, ensure_ascii=False, indent=2, default=str))
            print("\nEscopo:", llm.CONSENT_SCOPE)
            return 0
        if args.acao == "consentir":
            print(llm.CONSENT_SCOPE)
            print("A Anthropic processa os dados conforme a política dela; isso é uma transferência a terceiro.")
            if input('Digite AUTORIZO para conceder o consentimento: ').strip() != "AUTORIZO":
                print("Nada foi alterado.")
                return 1
            llm.grant_consent(cfg)
            print("Consentimento registrado. Defina [ia] modo = \"nuvem\" no config.toml para ativar.")
            return 0
        if args.acao == "revogar":
            llm.revoke_consent(cfg)
            print("Consentimento revogado; nenhum dado será enviado.")
            return 0
        print(json.dumps(llm.consent_status(cfg), ensure_ascii=False) or "sem consentimento")
        return 0

    if args.cmd == "testar-notificacao":
        from .notify import send
        print("Enviado por:", send(cfg, "healthmon: teste", "Notificação de teste (sem dados de saúde).", db) or "nenhum canal")
        return 0

    if args.cmd == "validar-garmin":
        from . import garmin_validation
        print("Validando a Garmin Connect Personal API. O relatório abaixo NÃO contém valores de saúde:\n"
              "só estados, nomes de campos, contagens e 'igual/diferente'.\n")
        rep = garmin_validation.run(cfg, db)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        print("\nRESULTADO:", "APROVADA" if rep["aprovado"] else "NÃO APROVADA", "-", rep.get("motivo", "ver etapas acima"))
        print(f"Relatório salvo em {cfg.home / 'validacao_garmin.json'}. Próximo passo: compare os valores com o app "
              "(painel → Configurações → Conferência com o app, ou `healthmon conferir-garmin`).")
        return 0 if rep["aprovado"] else 1

    if args.cmd == "conferir-garmin":
        from . import garmin_validation
        from .sync import today_local
        c = garmin_validation.comparison(db, today_local(cfg) - timedelta(days=1))
        print(f"Valores lidos para {c['dia']} — compare com o app Garmin Connect na mesma data.\n"
              "ATENÇÃO: estes são dados de saúde. Não cole esta saída em conversas com assistentes de IA.\n")
        for i in c["itens"]:
            print(f"  {i['rotulo']:32s} {i['valor'] or 'sem dado'}")
        print("\n" + c["onde_ver_no_app"])
        return 0

    if args.cmd == "kb":
        from . import kb
        if args.acao == "verificar":
            print(json.dumps(kb.verify(cfg), ensure_ascii=False, indent=1))
        elif args.acao == "buscar":
            for r in kb.search(args.termo or "", 6, cfg):
                print(f"- [{r['grau']}] {r['referencia']} {r['url'] or ''}")
        else:
            for r in kb.cite(list(kb.entries(cfg)), cfg):
                print(f"- {'✓' if r['verificado'] else '?'} {r['referencia']}")
        return 0

    if args.cmd == "alimentos":
        from . import nutrition
        if args.acao == "baixar-taco":
            print(nutrition.download_taco(db, args.url or nutrition.TACO_URL), "(fonte: TACO, NEPA/UNICAMP)")
        elif args.acao == "importar":
            print(nutrition.import_foods_csv(db, Path(args.arquivo), args.fonte))
        else:
            print(db.q("SELECT source, COUNT(*) n FROM foods GROUP BY source"))
        return 0

    if args.cmd == "exames":
        from . import labs
        print(f"{labs.import_csv(db, Path(args.arquivo))} resultado(s) importado(s).")
        return 0

    if args.cmd == "plano":
        from . import planner
        from .sync import today_local
        for i in planner.daily_plan(db, cfg, today_local(cfg))["itens"]:
            print(f"\n{i['pergunta']}\n  → {i['o_que_fazer']}\n  Por quê: {i['por_que']}\n  Confiança: {i['confianca']}")
        return 0

    if args.cmd == "exportar":
        from .config import write_private_file
        tables = ["daily_metrics", "activities", "reports", "alerts", "body_measurements", "meals", "checkins",
                  "training_plans", "workout_sets", "workout_sessions", "labs", "experiments", "experiment_days",
                  "decisions", "user_supplements", "raw_payloads"]
        dump = {t: db.q(f"SELECT * FROM {t}") for t in tables}
        dump["perfil"], dump["objetivos"] = db.get_json("perfil", {}), db.get_json("objetivos", [])
        write_private_file(Path(args.destino), json.dumps(dump, ensure_ascii=False))
        print(f"Exportado para {args.destino} (permissão 600). O arquivo NÃO é criptografado: guarde com cuidado.")
        return 0

    if args.cmd == "apagar-dados":
        import shutil
        if not args.confirmar or input('Isto apaga TUDO de forma irreversível. Digite APAGAR: ').strip() != "APAGAR":
            print("Nada foi apagado. Use --confirmar e digite APAGAR.")
            return 1
        for name in ("healthmon.db", "dashboard_auth.json", "vapid.json", "kb_verificacao.json", "kb_extra.json",
                     "consentimento_ia_nuvem.json", "healthmon.log", "secrets.env", "config.toml",
                     "recovery_public.key", "recovery_fingerprint", "offsite_state.json", "validacao_garmin.json", "servico.ativo"):
            (cfg.home / name).unlink(missing_ok=True)
        for f in list(cfg.home.glob("healthmon*.db*")) + [cfg.db_path, Path(str(cfg.db_path) + "-wal"), Path(str(cfg.db_path) + "-shm")]:
            f.unlink(missing_ok=True)
        for d in (cfg.home / "tokens", cfg.home / "relatorios", cfg.home / "exames", cfg.home / "backups", cfg.backup_dir):
            shutil.rmtree(d, ignore_errors=True)
        left = [f.name for f in cfg.home.iterdir()] if cfg.home.exists() else []
        print("Restou na pasta de dados:", ", ".join(left) or "nada")
        print("Apague também, se existirem: arquivos de exportação, cópias de backup fora do servidor e /tmp/validacao-garmin.txt.")
        print("Dados apagados. Para encerrar o acesso à Garmin, revogue a chave pessoal no site da Garmin.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
