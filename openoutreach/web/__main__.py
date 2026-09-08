"""Run with `openoutreach-web` or `python -m openoutreach.web`."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="Painel local do OpenOutreach")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", help="Caminho alternativo para o banco SQLite")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("A porta deve estar entre 1024 e 65535.")
    if args.db:
        os.environ["OPENOUTREACH_DB"] = args.db
    os.environ["DJANGO_SETTINGS_MODULE"] = "openoutreach.web.settings"
    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", interactive=False, verbosity=0)
    print(f"\nOpenOutreach → http://127.0.0.1:{args.port}\n", flush=True)
    call_command("runserver", f"127.0.0.1:{args.port}", use_reloader=False)


if __name__ == "__main__":
    main()
