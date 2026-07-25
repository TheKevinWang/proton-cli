"""Command-line parser for proton-cli."""

from __future__ import annotations

from typing import TypedDict

from proton_cli.errors import CliError
from proton_cli.types import (
    BrowserCloseCommand,
    BrowserMode,
    BrowserStatusCommand,
    GlobalOptions,
    HelpCommand,
    InboxCommand,
    LoginCommand,
    ParsedCommand,
    ParsedInvocation,
    ReadCommand,
    RecoveryEmailAddCommand,
    RefreshCommand,
    SendCommand,
)

DEFAULT_LIMIT = 20


def parse_argv(argv: list[str]) -> ParsedInvocation:
    extracted = _extract_global(argv)
    global_options = extracted["global_options"]
    rest = extracted["rest"]

    if global_options.session != "default":
        raise CliError(
            "Only --session default is supported in the MVP.",
            code="SESSION_NOT_IMPLEMENTED",
            exit_code=2,
        )
    if global_options.cdp is not None and global_options.debug_port is not None:
        raise CliError(
            "--cdp and --debug-port are mutually exclusive.",
            code="CONFLICTING_BROWSER_OPTIONS",
            exit_code=2,
        )

    command = _parse_command(rest, global_options)
    return ParsedInvocation(global_options=global_options, command=command)


class _ExtractedGlobal(TypedDict):
    global_options: GlobalOptions
    rest: list[str]


def _extract_global(argv: list[str]) -> _ExtractedGlobal:
    global_options = GlobalOptions()
    rest: list[str] = []

    index = 0
    while index < len(argv):
        arg = argv[index]
        value_flag = _split_flag_value(arg)

        if arg == "--json":
            global_options.json = True
            index += 1
            continue
        if arg in ("--verbose", "-v"):
            global_options.verbose = True
            index += 1
            continue
        if arg == "--keep-open":
            global_options.keep_open = True
            index += 1
            continue
        if value_flag is not None and _is_global_value_flag(value_flag["name"]):
            _set_global_value(global_options, value_flag["name"], value_flag["value"])
            index += 1
            continue
        if _is_global_value_flag(arg):
            value = argv[index + 1] if index + 1 < len(argv) else None
            if value is None:
                raise CliError(
                    f"Missing value for {arg}.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            _set_global_value(global_options, arg, value)
            index += 2
            continue
        rest.append(arg)
        index += 1

    return _ExtractedGlobal(global_options=global_options, rest=rest)


def _parse_command(args: list[str], global_options: GlobalOptions) -> ParsedCommand:
    if not args or args[0] in ("help", "--help", "-h"):
        return HelpCommand()

    name, *rest = args
    if name == "login":
        return _parse_login(rest)
    if name == "inbox":
        return _parse_inbox(rest)
    if name == "refresh":
        _reject_mode_flags(rest)
        return RefreshCommand(limit=_parse_limit(rest))
    if name == "read":
        return _parse_read(rest)
    if name == "send":
        return _parse_send(rest)
    if name == "recovery-email":
        return _parse_recovery_email(rest)
    if name == "browser":
        return _parse_browser(rest, global_options)
    raise CliError(f"Unknown command: {name}", code="UNKNOWN_COMMAND", exit_code=2)


def _parse_login(args: list[str]) -> LoginCommand:
    email: str | None = None
    password_env: str | None = None
    password: str | None = None
    headed = False
    headless = False

    index = 0
    while index < len(args):
        arg = args[index]
        with_value = _split_flag_value(arg)
        if with_value is not None and with_value["name"] == "--email":
            email = with_value["value"]
            index += 1
            continue
        if with_value is not None and with_value["name"] == "--password-env":
            password_env = with_value["value"]
            index += 1
            continue
        if with_value is not None and with_value["name"] == "--password":
            password = with_value["value"]
            index += 1
            continue
        if arg == "--headed":
            headed = True
            index += 1
            continue
        if arg == "--headless":
            headless = True
            index += 1
            continue
        if arg in ("--email", "--password-env", "--password"):
            value = args[index + 1] if index + 1 < len(args) else None
            if value is None:
                raise CliError(
                    f"Missing value for {arg}.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            if arg == "--email":
                email = value
            elif arg == "--password-env":
                password_env = value
            else:
                password = value
            index += 2
            continue
        raise CliError(f"Unknown login option: {arg}", code="UNKNOWN_OPTION", exit_code=2)

    if email is None:
        raise CliError(
            "login requires --email <address>.",
            code="MISSING_EMAIL",
            exit_code=2,
        )
    if headed and headless:
        raise CliError(
            "--headed and --headless are mutually exclusive.",
            code="CONFLICTING_BROWSER_MODE",
            exit_code=2,
        )
    mode: BrowserMode = "headless" if headless else "headed"
    return LoginCommand(email=email, password_env=password_env, password=password, mode=mode)


def _parse_inbox(args: list[str]) -> InboxCommand | RefreshCommand:
    _reject_mode_flags(args)
    if args and args[0] == "refresh":
        return RefreshCommand(limit=_parse_limit(args[1:]))

    folder = "inbox"
    limit = DEFAULT_LIMIT

    index = 0
    while index < len(args):
        arg = args[index]
        with_value = _split_flag_value(arg)
        if with_value is not None and with_value["name"] == "--limit":
            limit = _parse_positive_int(with_value["value"], "--limit")
            index += 1
            continue
        if with_value is not None and with_value["name"] == "--folder":
            folder = with_value["value"]
            index += 1
            continue
        if arg == "--limit":
            value = args[index + 1] if index + 1 < len(args) else None
            if value is None:
                raise CliError(
                    "Missing value for --limit.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            limit = _parse_positive_int(value, "--limit")
            index += 2
            continue
        if arg == "--folder":
            value = args[index + 1] if index + 1 < len(args) else None
            if value is None:
                raise CliError(
                    f"Missing value for {arg}.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            folder = value
            index += 2
            continue
        raise CliError(f"Unknown inbox option: {arg}", code="UNKNOWN_OPTION", exit_code=2)

    normalized_folder = folder.lower()
    if normalized_folder not in ("inbox", "sent"):
        raise CliError(
            "Only inbox --folder inbox or --folder sent is supported.",
            code="FOLDER_NOT_IMPLEMENTED",
            exit_code=2,
        )
    return InboxCommand(limit=limit, folder=normalized_folder)  # type: ignore[arg-type]


def _parse_read(args: list[str]) -> ReadCommand:
    _reject_mode_flags(args)
    handle: str | None = None

    for arg in args:
        if arg == "--overwrite":
            raise _attachment_download_error()
        with_value = _split_flag_value(arg)
        if with_value is not None and with_value["name"] == "--download-attachments":
            raise _attachment_download_error()
        if arg == "--download-attachments":
            raise _attachment_download_error()
        if arg.startswith("--"):
            raise CliError(f"Unknown read option: {arg}", code="UNKNOWN_OPTION", exit_code=2)
        if handle is not None:
            raise CliError(
                "read accepts exactly one message handle.",
                code="TOO_MANY_ARGUMENTS",
                exit_code=2,
            )
        handle = arg

    if handle is None:
        raise CliError(
            "read requires a message handle from the latest inbox output.",
            code="MISSING_MESSAGE_HANDLE",
            exit_code=2,
        )
    return ReadCommand(handle=handle)


def _parse_send(args: list[str]) -> SendCommand:
    _reject_mode_flags(args)
    command = SendCommand()

    index = 0
    while index < len(args):
        arg = args[index]
        with_value = _split_flag_value(arg)
        if with_value is not None and _is_send_value_flag(with_value["name"]):
            _set_send_value(command, with_value["name"], with_value["value"])
            index += 1
            continue
        if _is_send_value_flag(arg):
            value = args[index + 1] if index + 1 < len(args) else None
            if value is None:
                raise CliError(
                    f"Missing value for {arg}.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            _set_send_value(command, arg, value)
            index += 2
            continue
        raise CliError(f"Unknown send option: {arg}", code="UNKNOWN_OPTION", exit_code=2)

    return command


def _parse_browser(args: list[str], global_options: GlobalOptions) -> ParsedCommand:
    _reject_mode_flags(args)
    if not args:
        raise CliError(
            "browser requires status or close.",
            code="UNKNOWN_BROWSER_COMMAND",
            exit_code=2,
        )
    subcommand, *rest = args
    if subcommand == "status":
        if rest:
            raise CliError(
                f"Unknown browser status option: {rest[0]}",
                code="UNKNOWN_OPTION",
                exit_code=2,
            )
        return BrowserStatusCommand()
    if subcommand == "close":
        if global_options.keep_open:
            raise CliError(
                "Cannot combine --keep-open with browser close.",
                code="KEEP_OPEN_CLOSE_CONFLICT",
                exit_code=2,
            )
        force = False
        for arg in rest:
            if arg == "--force":
                force = True
            else:
                raise CliError(
                    f"Unknown browser close option: {arg}",
                    code="UNKNOWN_OPTION",
                    exit_code=2,
                )
        return BrowserCloseCommand(force=force)
    raise CliError(
        "browser requires status or close.",
        code="UNKNOWN_BROWSER_COMMAND",
        exit_code=2,
    )


def _parse_recovery_email(args: list[str]) -> RecoveryEmailAddCommand:
    _reject_mode_flags(args)
    if not args or args[0] != "add":
        raise CliError(
            "recovery-email requires the add subcommand.",
            code="UNKNOWN_RECOVERY_EMAIL_COMMAND",
            exit_code=2,
        )
    command = RecoveryEmailAddCommand()
    value_flags = {
        "--email",
        "--password-env",
        "--verification",
        "--recovery-password-env",
        "--recovery-proxy",
        "--timeout-seconds",
    }
    index = 1
    while index < len(args):
        arg = args[index]
        with_value = _split_flag_value(arg)
        if with_value is not None and with_value["name"] in value_flags:
            name = with_value["name"]
            value = with_value["value"]
            index += 1
        elif arg in value_flags:
            if index + 1 >= len(args):
                raise CliError(
                    f"Missing value for {arg}.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            name = arg
            value = args[index + 1]
            index += 2
        else:
            raise CliError(
                f"Unknown recovery-email option: {arg}",
                code="UNKNOWN_OPTION",
                exit_code=2,
            )

        if name == "--email":
            command.email = value
        elif name == "--password-env":
            command.password_env = value
        elif name == "--verification":
            if value not in {"auto", "proton", "interactive"}:
                raise CliError(
                    "--verification must be auto, proton, or interactive.",
                    code="INVALID_RECOVERY_VERIFICATION",
                    exit_code=2,
                )
            command.verification = value  # type: ignore[assignment]
        elif name == "--recovery-password-env":
            command.recovery_password_env = value
        elif name == "--recovery-proxy":
            if not value.startswith("socks5://"):
                raise CliError(
                    f"--recovery-proxy must be a socks5:// URL, got: {value}",
                    code="INVALID_PROXY",
                    exit_code=2,
                )
            command.recovery_proxy = value
        elif name == "--timeout-seconds":
            command.timeout_seconds = _parse_positive_int(value, name)

    if not command.email.strip():
        raise CliError(
            "recovery-email add requires --email <address>.",
            code="MISSING_EMAIL",
            exit_code=2,
        )
    return command


def _parse_limit(args: list[str]) -> int:
    limit = DEFAULT_LIMIT
    index = 0
    while index < len(args):
        arg = args[index]
        with_value = _split_flag_value(arg)
        if with_value is not None and with_value["name"] == "--limit":
            limit = _parse_positive_int(with_value["value"], "--limit")
            index += 1
            continue
        if arg == "--limit":
            value = args[index + 1] if index + 1 < len(args) else None
            if value is None:
                raise CliError(
                    "Missing value for --limit.",
                    code="MISSING_FLAG_VALUE",
                    exit_code=2,
                )
            limit = _parse_positive_int(value, "--limit")
            index += 2
            continue
        raise CliError(f"Unknown refresh option: {arg}", code="UNKNOWN_OPTION", exit_code=2)
    return limit


def _split_flag_value(arg: str) -> dict[str, str] | None:
    if not arg.startswith("--"):
        return None
    equals_index = arg.find("=")
    if equals_index < 0:
        return None
    return {"name": arg[:equals_index], "value": arg[equals_index + 1 :]}


def _is_global_value_flag(name: str) -> bool:
    return name in (
        "--session",
        "--cdp",
        "--debug-port",
        "--chrome-data-dir",
        "--trace",
        "--proxy",
    )


def _set_global_value(global_options: GlobalOptions, name: str, value: str) -> None:
    if name == "--session":
        global_options.session = value
    elif name == "--cdp":
        global_options.cdp = value
    elif name == "--trace":
        global_options.trace = value
    elif name == "--debug-port":
        global_options.debug_port = _parse_positive_int(value, "--debug-port")
    elif name == "--chrome-data-dir":
        if not value.strip():
            raise CliError(
                "--chrome-data-dir requires a non-empty path.",
                code="MISSING_CHROME_DATA_DIR",
                exit_code=2,
            )
        global_options.chrome_data_dir = value
    elif name == "--proxy":
        if not value.strip():
            raise CliError(
                "--proxy requires a non-empty socks5:// URL.",
                code="MISSING_PROXY",
                exit_code=2,
            )
        if not value.startswith("socks5://"):
            raise CliError(
                f"--proxy must be a socks5:// URL, got: {value}",
                code="INVALID_PROXY",
                exit_code=2,
            )
        global_options.proxy = value


def _is_send_value_flag(name: str) -> bool:
    return name in ("--to", "--cc", "--bcc", "--subject", "--body", "--body-file", "--attach")


def _set_send_value(command: SendCommand, name: str, value: str) -> None:
    if name == "--to":
        command.to.append(value)
    elif name == "--cc":
        command.cc.append(value)
    elif name == "--bcc":
        command.bcc.append(value)
    elif name == "--subject":
        command.subject = value
    elif name == "--body":
        command.body = value
    elif name == "--body-file":
        command.body_file = value
    elif name == "--attach":
        command.attachments.append(value)


def _parse_positive_int(value: str, flag: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CliError(
            f"{flag} must be a positive integer.", code="INVALID_NUMBER", exit_code=2
        ) from exc
    if parsed <= 0 or str(parsed) != value.strip():
        raise CliError(f"{flag} must be a positive integer.", code="INVALID_NUMBER", exit_code=2)
    return parsed


def _reject_mode_flags(args: list[str]) -> None:
    for arg in args:
        if arg in ("--headed", "--headless"):
            raise CliError(
                f"{arg} is only supported by login.",
                code="BROWSER_MODE_NOT_ALLOWED",
                exit_code=2,
            )


def _attachment_download_error() -> CliError:
    return CliError(
        "Attachment downloads from read are not implemented in the MVP.",
        code="ATTACHMENT_DOWNLOAD_NOT_IMPLEMENTED",
        exit_code=2,
    )
