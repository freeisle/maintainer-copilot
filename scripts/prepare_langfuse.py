"""Prepare an isolated local deployment; never start containers or overwrite secrets."""
import argparse
import hashlib
from pathlib import Path
import secrets
import re
import httpx
import yaml

VERSION = "v4.63.0"
SHA256 = "565c9ec6453cb317671b44d1f96f0364883dde8cc0451d67da77c08b1797c1a1"
URL = f"https://raw.githubusercontent.com/langfuse/langfuse/{VERSION}/docker-compose.yml"


def prepare(raw, destination):
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError("Upstream compose checksum mismatch")
    destination = Path(destination)
    if (destination / ".env").exists() or (destination / "compose.yml").exists():
        raise FileExistsError("Deployment already prepared; refusing to replace credentials")
    document = yaml.safe_load(raw)
    services = document["services"]
    for name,service in services.items():
        if name.startswith("langfuse-"):
            service["image"] = service["image"].rsplit(":", 1)[0] + ":4.63.0"
        # Only UI and media endpoints need host exposure. Isolate Postgres from RAG.
        service.pop("ports", None)
    services["langfuse-web"]["ports"] = ["127.0.0.1:3000:3000"]
    services["minio"]["ports"] = ["127.0.0.1:9090:9000"]
    password, redis, clickhouse, storage = [secrets.token_hex(24) for _ in range(4)]
    values = {"POSTGRES_PASSWORD":password,"DATABASE_URL":f"postgresql://postgres:{password}@postgres:5432/postgres",
        "REDIS_AUTH":redis,"CLICKHOUSE_PASSWORD":clickhouse,"MINIO_ROOT_PASSWORD":storage,
        "SALT":secrets.token_hex(32),"ENCRYPTION_KEY":secrets.token_hex(32),"NEXTAUTH_SECRET":secrets.token_hex(32),
        "TELEMETRY_ENABLED":"false"}
    for prefix in ("EVENT_UPLOAD","MEDIA_UPLOAD","BATCH_EXPORT"):
        values[f"LANGFUSE_S3_{prefix}_SECRET_ACCESS_KEY"] = storage
    rendered = yaml.safe_dump(document, sort_keys=False)
    for key in values:
        rendered = re.sub(r"\$\{"+key+r":-[^}]*\}", "${"+key+":?Set deployment .env}", rendered)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "compose.yml").write_text(rendered,encoding="utf-8")
    with (destination / ".env").open("x",encoding="utf-8") as f:
        f.write("\n".join(f"{k}={v}" for k,v in values.items())+"\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,help="Previously downloaded official compose")
    parser.add_argument("--output",type=Path,default=Path("data/langfuse"))
    args = parser.parse_args()
    if args.source:
        raw = args.source.read_bytes()
    else:
        response = httpx.get(URL,timeout=30,follow_redirects=True)
        response.raise_for_status()
        raw = response.content
    print(prepare(raw,args.output))
