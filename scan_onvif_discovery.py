import argparse
from ipaddress import ip_address
from pprint import pformat

try:
    from wsdiscovery import WSDiscovery
except ImportError:
    WSDiscovery = None

try:
    from wsdiscovery.discovery import ThreadedWSDiscovery
except Exception:
    ThreadedWSDiscovery = None


def _safe_call(obj, method_name, default=None):
    method = getattr(obj, method_name, None)
    if callable(method):
        try:
            return method()
        except Exception as exc:
            return f"<error calling {method_name}: {exc}>"
    return default


def main():
    parser = argparse.ArgumentParser(
        description="Scan ONVIF devices via WS-Discovery and print raw service details."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="WS-Discovery search timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--bind-ip",
        default="0.0.0.0",
        help="Local bind IP to annotate in logs. Current WSDiscovery does not explicitly bind it.",
    )
    args = parser.parse_args()

    if WSDiscovery is None:
        print("Missing dependency: wsdiscovery")
        print("Install project dependencies first, for example:")
        print("  pip install -r requirements.txt")
        print("Or run the script with the Python environment where WSDiscovery is already installed.")
        return 1

    print("Starting WS-Discovery scan for ONVIF services...")
    wsd = None
    try:
        wsd = WSDiscovery()
    except Exception as exc:
        print(f"WSDiscovery() init failed: {exc}")

    if wsd is None and ThreadedWSDiscovery is not None:
        try:
            wsd = ThreadedWSDiscovery()
            print("Fallback to ThreadedWSDiscovery.")
        except Exception as exc:
            print(f"ThreadedWSDiscovery init failed: {exc}")
            return 1

    if wsd is None:
        print("Failed to initialize WS-Discovery instance.")
        return 1

    wsd.start()
    if args.bind_ip not in ("", "0.0.0.0"):
        try:
            src_addr = ip_address(args.bind_ip)
            if hasattr(wsd, "addSourceAddr") and callable(getattr(wsd, "addSourceAddr")):
                wsd.addSourceAddr(src_addr)
                print(f"WS-Discovery source address bound to: {src_addr}")
            else:
                print(
                    "Current WS-Discovery implementation does not support addSourceAddr; "
                    "using system default interface."
                )
        except Exception as exc:
            print(
                f"Failed to bind bind-ip={args.bind_ip}, fallback to default interface: {exc}"
            )

    try:
        services = wsd.searchServices(timeout=args.timeout)
        print(f"Discovered {len(services)} raw service(s).")

        if not services:
            print("No WS-Discovery services found.")
            return

        for index, service in enumerate(services, start=1):
            print("=" * 80)
            print(f"Service #{index}")
            print(f"EPR: {_safe_call(service, 'getEPR', '<unavailable>')}")
            print(f"Instance ID: {_safe_call(service, 'getInstanceId', '<unavailable>')}")
            print(f"Message Number: {_safe_call(service, 'getMessageNumber', '<unavailable>')}")

            xaddrs = _safe_call(service, 'getXAddrs', [])
            scopes = _safe_call(service, 'getScopes', [])
            types = _safe_call(service, 'getTypes', [])

            print("Types:")
            print(pformat(types))
            print("Scopes:")
            print(pformat(scopes))
            print("XAddrs:")
            print(pformat(xaddrs))

            print("Raw service object:")
            print(repr(service))
    finally:
        wsd.stop()
        print("WS-Discovery scan finished.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())