"""Write the app.bsky.feed.generator record into the publisher's repo.

Needs BSKY_HANDLE and BSKY_APP_PASSWORD (an app password, not the main one), and
BSKY_PDS if the account isn't on bsky.social.
"""

import argparse
import os
from datetime import UTC, datetime

from atproto import Client
from curator_core.config import core_settings

parser = argparse.ArgumentParser()
parser.add_argument("--name", default="Curated for you")
parser.add_argument(
    "--description", default="An LLM-curated feed tuned to what you describe. Set it up at the linked site."
)
args = parser.parse_args()

cfg = core_settings()
client = Client(base_url=os.environ.get("BSKY_PDS", "https://bsky.social"))
profile = client.login(os.environ["BSKY_HANDLE"], os.environ["BSKY_APP_PASSWORD"])
record = {
    "$type": "app.bsky.feed.generator",
    "did": cfg.service_did,
    "displayName": args.name,
    "description": args.description,
    "acceptsInteractions": True,
    "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
}
client.com.atproto.repo.put_record(
    {"repo": profile.did, "collection": "app.bsky.feed.generator", "rkey": cfg.feed_rkey, "record": record}
)
print(f"published at://{profile.did}/app.bsky.feed.generator/{cfg.feed_rkey}")
if profile.did != cfg.publisher_did:
    print(f"set PUBLISHER_DID={profile.did}")
