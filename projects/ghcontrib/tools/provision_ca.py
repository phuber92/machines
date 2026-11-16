"""Save the root of GitHub's verified chain using the host trust store."""

import pathlib
import socket
import ssl

PROJECT = pathlib.Path(__file__).resolve().parents[1]


def main():
    context = ssl.create_default_context()
    with socket.create_connection(("api.github.com", 443), timeout=20) as sock:
        with context.wrap_socket(sock, server_hostname="api.github.com") as tls:
            get_chain = getattr(tls, "get_verified_chain", None)
            if get_chain is None:
                raise RuntimeError("Use Python 3.13 or newer")
            (PROJECT / "github-ca.der").write_bytes(get_chain()[-1])
    print("Saved verified GitHub root certificate to projects/ghcontrib/github-ca.der")


if __name__ == "__main__":
    main()
