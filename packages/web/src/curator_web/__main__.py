import argparse
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="curator-web")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "curator_web.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
