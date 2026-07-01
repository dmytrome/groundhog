from .server import build_server


def main() -> None:
    # stdio is the only transport for now; HTTP support ships with its own
    # host/DNS-rebinding hardening.
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
