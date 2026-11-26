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
    rs = sub.add_parser("restaurar", help="restaura um backup .hmbk")
    rs.add_argument("arquivo")
    dm = sub.add_parser("demo", help="popula um banco com dados SIMULADOS (use HEALTHMON_HOME separado)")
    dm.add_argument("--dias", type=int, default=120)
    ia = sub.add_parser("ia-nuvem", help="consentimento para IA em nuvem")
    ia.add_argument("acao", choices=["previa", "consentir", "revogar", "status"])
    sub.add_parser("testar-notificacao", help="envia mensagem de teste pelos canais configurados")
    sub.add_parser("validar-garmin", help="testa a API oficial e mostra a ESTRUTURA das respostas (sem valores)")
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
              "- Depois: healthmon login && healthmon senha-dashboard && healthmon historico --dias 90")
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
        if args.cmd == "historico":
            days = svc.pending_days(n)
            print(f"Importando {len(days)} de {n} dias (os demais já estão gravados). Há pausas e orçamento diário de "
                  "requisições; se o limite for atingido, repita o comando depois: ele continua de onde parou.")
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
        try:
            uvicorn.run(app, host=host, port=port, server_header=False, date_header=False,
                        proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_level="warning")
        finally:
            if sched:
                sched.shutdown(wait=False)
        return 0

    if args.cmd == "backup":
        from .backup import create_backup
        p = create_backup(cfg, Path(args.destino) if args.destino else None)
        print(f"Backup criptografado: {p}")
        return 0

    if args.cmd == "restaurar":
        from .backup import restore_backup
        print(f"Banco restaurado em {restore_backup(cfg, Path(args.arquivo))} (cópia anterior preservada).")
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
            facts = llm.strip_identifiers({"dia": day.isoformat(), "recuperacao": snap["recuperacao"],
                                           "avaliacoes": snap["avaliacoes"], "ausentes": snap["ausentes"]})
            print("EXATAMENTE este tipo de conteúdo seria enviado à Anthropic a cada relatório:\n")
            print(json.dumps(facts, ensure_ascii=False, indent=2))
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
        from .garmin_official import ENDPOINTS, PersonalApiSource, key_expiry_status, shape
        from .parse import parse_day
        from .sync import today_local
        from .db import utcnow
        src = PersonalApiSource(cfg, db)
        day = (today_local(cfg) - timedelta(days=1)).isoformat()
        print(f"Validando a Garmin Personal API com os dados de {day}. Abaixo aparecem apenas nomes de campos e tipos.\n")
        try:
            fetched = src.fetch_day(day)
        except Exception as e:  # noqa: BLE001
            print(f"FALHA: {type(e).__name__}: {e}")
            return 1
        ok = 0
        for name in ENDPOINTS:
            status, payload = fetched[name]
            ok += status in ("ok", "vazio")
            print(f"== {name}: {status}")
            if status == "ok":
                print("   ", json.dumps(shape(payload), ensure_ascii=False)[:1500])
                db.save_raw(day, name, status, payload)
        values = parse_day({k: v for k, (st, v) in fetched.items() if st == "ok"})
        print(f"\n{ok}/{len(ENDPOINTS)} endpoints responderam. Indicadores reconhecidos pelo parser ({len(values)}):")
        print("  ", ", ".join(sorted(values)) or "nenhum")
        print("Validade da chave:", key_expiry_status(cfg))
        if ok and values:
            db.set_setting("garmin_validado_em", utcnow())
            db.set_setting("data_source", "garmin")
            print("\nIntegração real validada. Se algum indicador esperado não foi reconhecido, envie ESTA saída "
                  "(não contém valores de saúde) para ajuste do parser.")
        return 0 if ok else 1

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
        for name in ("healthmon.db", "healthmon.db-wal", "healthmon.db-shm", "dashboard_auth.json", "vapid.json",
                     "kb_verificacao.json", "consentimento_ia_nuvem.json", "healthmon.log"):
            (cfg.home / name).unlink(missing_ok=True)
        for d in ("tokens", "relatorios", "exames", "backups"):
            shutil.rmtree(cfg.home / d, ignore_errors=True)
        print("Dados apagados. Para encerrar o acesso à Garmin, revogue a chave pessoal no site da Garmin.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
