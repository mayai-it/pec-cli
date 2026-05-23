"""Entry point for the `pec` CLI.

    pec auth login --address <addr> --provider <provider>
    pec auth status
    pec auth logout

    pec list [--folder inbox|sent] [--unread] [--from YYYY-MM-DD] [--limit N]
    pec get <id> [--folder F] [--save-attachments DIR]

    pec send --to <addr>... --subject <s> --body <text>
    pec send --to <addr>... --subject <s> --file body.txt --attach doc.pdf
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from pec_cli import __version__
from pec_cli.auth import (
    PROVIDERS,
    Credentials,
    delete_credentials,
    get_provider,
    load_credentials,
    save_credentials,
)
from pec_cli.imap import IMAPClient, IMAPError
from pec_cli.models.message import _is_cert_attachment
from pec_cli.output import emit, error
from pec_cli.smtp import SMTPError, send_pec


def _stdin_is_interactive() -> bool:
    """Indirection so tests can stub out the TTY check.

    `CliRunner` swaps `sys.stdin` to a `BytesIO`, so patching `sys.stdin.isatty`
    after the runner takes over doesn't stick — but patching this function does.
    """
    return sys.stdin.isatty()


# ---------------------------------------------------------------------------
# Shared CLI context + flag plumbing
# ---------------------------------------------------------------------------


class CLIContext:
    def __init__(self, as_json: bool, verbose: bool) -> None:
        self.as_json = as_json
        self.verbose = verbose

    def require_credentials(self) -> Credentials:
        try:
            creds = load_credentials()
        except RuntimeError as exc:
            error(str(exc))
            sys.exit(2)
        if creds is None:
            error("not authenticated — run `pec auth login` first")
            sys.exit(2)
        return creds


pass_ctx = click.make_pass_decorator(CLIContext)


_R = TypeVar("_R")


def common_flags(func: Callable[..., _R]) -> Callable[..., _R]:
    """Re-declare root `--json` / `--verbose` so flag position doesn't matter.

    The wrapper consumes `_local_json` / `_local_verbose` injected by click
    options below and forwards the rest to `func`, so the wrapped command
    never sees flags it didn't declare. This signature swap is why the type
    is `Callable[..., _R]` rather than a ParamSpec — the kwargs really do
    change between caller and callee.
    """

    @click.option("--json", "_local_json", is_flag=True, default=False,
                  help="Output one JSON object per line (NDJSON).")
    @click.option("--verbose", "_local_verbose", is_flag=True, default=False,
                  help="Log protocol details and timings to stderr.")
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> _R:
        local_json = kwargs.pop("_local_json", False)
        local_verbose = kwargs.pop("_local_verbose", False)
        cli_ctx = click.get_current_context().find_object(CLIContext)
        if cli_ctx is not None:
            if local_json:
                cli_ctx.as_json = True
            if local_verbose:
                cli_ctx.verbose = True
        return func(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="CLI for PEC (Posta Elettronica Certificata) — built for AI agents and developers.",
)
@click.version_option(__version__, prog_name="pec")
@click.option("--json", "as_json", is_flag=True, help="Output one JSON object per line (NDJSON).")
@click.option("--verbose", is_flag=True, help="Log protocol details and timings to stderr.")
@click.pass_context
def cli(ctx: click.Context, as_json: bool, verbose: bool) -> None:
    ctx.obj = CLIContext(as_json=as_json, verbose=verbose)
    if verbose:
        # Surface retry events (and any other pec.* loggers) on stderr.
        # WARNING-and-above is the right floor: retries are warnings, real
        # errors raise as exceptions and don't go through logging.
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("[pec] %(message)s"))
        pec_logger = logging.getLogger("pec")
        pec_logger.setLevel(logging.WARNING)
        # Avoid stacking handlers if the cli is re-entered (e.g. in tests).
        if not any(isinstance(h, logging.StreamHandler) for h in pec_logger.handlers):
            pec_logger.addHandler(handler)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@cli.group(help="Manage PEC account credentials.")
def auth() -> None: ...


@auth.command("login", help="Save credentials for a PEC account.")
@click.option("--address", required=True, help="Full PEC address (e.g. mia@pec.it).")
@click.option(
    "--provider",
    required=True,
    type=click.Choice(sorted(PROVIDERS.keys()), case_sensitive=False),
    help="PEC provider preset (aruba, legalmail, namirial, register, poste, pec.it).",
)
@common_flags
@pass_ctx
def auth_login(ctx: CLIContext, address: str, provider: str) -> None:
    try:
        pc = get_provider(provider)
    except KeyError as exc:
        error(str(exc))
        sys.exit(1)

    password = click.prompt("Password", hide_input=True, err=True)
    creds = Credentials(address=address, provider=pc.name, password=password)

    # Verify by attempting an IMAP login before persisting — saves the user
    # from a stored-but-broken setup.
    try:
        with IMAPClient(creds, verbose=ctx.verbose):
            pass
    except IMAPError as exc:
        error(str(exc))
        sys.exit(2)

    save_credentials(creds)

    emit(
        {
            "status": "ok",
            "address": creds.address,
            "provider": creds.provider,
            "imap": f"{pc.imap_host}:{pc.imap_port}",
            "smtp": f"{pc.smtp_host}:{pc.smtp_port}",
        },
        as_json=ctx.as_json,
    )


@auth.command("status", help="Show whether credentials are present.")
@common_flags
@pass_ctx
def auth_status(ctx: CLIContext) -> None:
    try:
        creds = load_credentials()
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(2)
    if creds is None:
        emit({"authenticated": False}, as_json=ctx.as_json)
        sys.exit(2)
    pc = creds.provider_config
    emit(
        {
            "authenticated": True,
            "address": creds.address,
            "provider": creds.provider,
            "imap": f"{pc.imap_host}:{pc.imap_port}",
            "smtp": f"{pc.smtp_host}:{pc.smtp_port}",
        },
        as_json=ctx.as_json,
    )


@auth.command("logout", help="Delete saved credentials and encryption key.")
@common_flags
@pass_ctx
def auth_logout(ctx: CLIContext) -> None:
    removed = delete_credentials()
    emit({"removed": removed}, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@cli.command("list", help="List PEC messages.")
@click.option(
    "--folder",
    default="inbox",
    show_default=True,
    help="Folder alias (inbox, sent) or raw IMAP folder name.",
)
@click.option("--unread", is_flag=True, help="Only unread messages.")
@click.option("--from", "since", help="Only messages on/after this date (YYYY-MM-DD).")
@click.option("--limit", type=int, default=20, show_default=True, help="Max messages to return.")
@common_flags
@pass_ctx
def list_messages(
    ctx: CLIContext,
    folder: str,
    unread: bool,
    since: str | None,
    limit: int,
) -> None:
    creds = ctx.require_credentials()

    try:
        with IMAPClient(creds, verbose=ctx.verbose) as client:
            client.select_folder(folder)
            uids = client.search(unread=unread, since=since)
            # IMAP returns oldest-first; we want newest first and capped.
            uids = uids[-limit:][::-1] if limit else uids[::-1]
            summaries = client.fetch_summaries(uids)
    except IMAPError as exc:
        error(str(exc))
        sys.exit(1)

    rows: list[dict[str, Any]] = []
    for s in summaries:
        row = s.to_dict()
        # Drop noisy long `to` list from human view when message is from us.
        if not ctx.verbose:
            row.pop("to", None)
        rows.append(row)

    emit(rows, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@cli.command("get", help="Fetch a single PEC message by id (IMAP UID).")
@click.argument("message_id")
@click.option(
    "--folder",
    default="inbox",
    show_default=True,
    help="Folder alias (inbox, sent) or raw IMAP folder name.",
)
@click.option(
    "--save-attachments",
    "save_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Save attachments to the given directory (e.g. ./attachments).",
)
@click.option(
    "--cert",
    "show_cert",
    is_flag=True,
    default=False,
    help="Include the parsed daticert.xml certification (tipo, mittente, identificativo, ...).",
)
@common_flags
@pass_ctx
def get_message(
    ctx: CLIContext,
    message_id: str,
    folder: str,
    save_dir: Path | None,
    show_cert: bool,
) -> None:
    creds = ctx.require_credentials()

    try:
        with IMAPClient(creds, verbose=ctx.verbose) as client:
            client.select_folder(folder)
            message = client.fetch_message(message_id)
    except IMAPError as exc:
        error(str(exc))
        sys.exit(1)

    saved: list[str] = []
    if save_dir is not None and message.attachments:
        save_dir.mkdir(parents=True, exist_ok=True)
        for att in message.attachments:
            if not ctx.verbose and _is_cert_attachment(att):
                continue
            if att.data is None:
                continue
            target = _safe_attachment_path(save_dir, att.filename)
            target.write_bytes(att.data)
            saved.append(str(target))

    payload = message.to_dict(
        include_html=ctx.verbose,
        include_cert=show_cert or ctx.verbose,
        include_cert_xml=ctx.verbose,
    )
    if saved:
        payload["saved_attachments"] = saved
    emit(payload, as_json=ctx.as_json)


def _safe_attachment_path(out_dir: Path, filename: str) -> Path:
    """Strip path separators from the attachment name to avoid directory escape."""
    safe = Path(filename).name or "attachment"
    return out_dir / safe


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


@cli.command("send", help="Send a PEC message.")
@click.option("--to", "to", multiple=True, required=True, help="Recipient address (repeatable).")
@click.option("--cc", multiple=True, help="CC address (repeatable).")
@click.option("--subject", required=True, help="Subject line.")
@click.option("--body", help="Inline body text. Use --file to read from a file instead.")
@click.option(
    "--file",
    "body_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read body text from this file.",
)
@click.option(
    "--attach",
    "attachments",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Attach a file (repeatable).",
)
@click.option("--dry-run", is_flag=True, help="Don't actually send — print what would be sent.")
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip the interactive 'are you sure' prompt. Required when stdin is not a TTY.",
)
@common_flags
@pass_ctx
def send(
    ctx: CLIContext,
    to: tuple[str, ...],
    cc: tuple[str, ...],
    subject: str,
    body: str | None,
    body_file: Path | None,
    attachments: tuple[Path, ...],
    dry_run: bool,
    yes: bool,
) -> None:
    if body is None and body_file is None:
        error("either --body or --file is required")
        sys.exit(1)
    if body is not None and body_file is not None:
        error("--body and --file are mutually exclusive")
        sys.exit(1)

    if body is not None:
        body_text = body
    else:
        # The pair of guard-checks above prove body_file is not None here.
        assert body_file is not None
        body_text = body_file.read_text(encoding="utf-8")

    creds = ctx.require_credentials()

    if dry_run:
        emit(
            {
                "status": "dry-run",
                "from": creds.address,
                "to": list(to),
                "cc": list(cc),
                "subject": subject,
                "body_length": len(body_text),
                "attachments": [str(p) for p in attachments],
            },
            as_json=ctx.as_json,
        )
        return

    # PEC has the legal value of a registered letter — gate the send behind an
    # explicit confirmation. Interactive TTY: prompt. Non-interactive: require
    # --yes so a misconfigured script can't silently fire off a legal email.
    if not yes:
        if not _stdin_is_interactive():
            error(
                "non-interactive shell — pass --yes to confirm sending a PEC "
                "(legally binding, equivalent to a registered letter)"
            )
            sys.exit(3)
        recipients = ", ".join(to)
        click.echo(
            f"About to send a PEC to {recipients} (subject: {subject!r}).",
            err=True,
        )
        click.echo(
            "PEC has the legal value of a registered letter (raccomandata).",
            err=True,
        )
        if not click.confirm("Confirm send?", default=False, err=True):
            click.echo("Aborted.", err=True)
            sys.exit(0)

    try:
        result = send_pec(
            creds,
            to=list(to),
            cc=list(cc) or None,
            subject=subject,
            body=body_text,
            attachments=list(attachments) or None,
            verbose=ctx.verbose,
        )
    except SMTPError as exc:
        error(str(exc))
        sys.exit(1)

    emit(result, as_json=ctx.as_json)


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------


@cli.command("trace", help="Trace the receipt chain for a PEC message id.")
@click.argument("target_message_id")
@click.option(
    "--folder",
    default="inbox",
    show_default=True,
    help="Folder to scan (inbox is where receipts arrive).",
)
@click.option(
    "--limit",
    type=int,
    default=200,
    show_default=True,
    help="Max recent PEC receipts to scan in the folder.",
)
@common_flags
@pass_ctx
def trace(
    ctx: CLIContext,
    target_message_id: str,
    folder: str,
    limit: int,
) -> None:
    target = target_message_id.strip().lstrip("<").rstrip(">").strip()
    if not target:
        error("message id is empty")
        sys.exit(1)

    creds = ctx.require_credentials()

    try:
        with IMAPClient(creds, verbose=ctx.verbose) as client:
            client.select_folder(folder)
            uids = client.search()
            uids = uids[-limit:] if limit else uids
            # fetch summaries first so we only open the bodies of PEC receipts
            summaries = client.fetch_summaries(uids)
            pec_uids = [s.id for s in summaries if s.pec_type]

            chain: list[dict[str, Any]] = []
            for uid in pec_uids:
                msg = client.fetch_message(uid)
                if msg.daticert is None:
                    continue
                if msg.daticert.riferimento_message_id != target:
                    continue
                err = msg.daticert.errore
                chain.append({
                    "id": msg.id,
                    "tipo": msg.daticert.tipo,
                    "data": msg.daticert.data,
                    "identificativo": msg.daticert.identificativo,
                    "mittente": msg.daticert.mittente,
                    "destinatari": msg.daticert.destinatari,
                    "errore": err if err and err != "nessuno" else None,
                })
    except IMAPError as exc:
        error(str(exc))
        sys.exit(1)

    chain.sort(key=lambda r: r.get("data") or "")

    emit(
        {
            "message_id": target,
            "events": chain,
            "count": len(chain),
        },
        as_json=ctx.as_json,
    )


if __name__ == "__main__":
    cli()
