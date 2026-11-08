import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poly_paper.fee import contracts_from_dollars, cost, fee  # noqa: E402

FIXTURES = ROOT / "fixtures"
POLICY_SRC = ROOT / "poly_paper"


@pytest.fixture
def policy_dir(tmp_path):
    dest = tmp_path / "policy"
    dest.mkdir()
    shutil.copy(POLICY_SRC / "effective_policy.json", dest / "effective_policy.json")
    shutil.copy(POLICY_SRC / "type_gate.snapshot.json", dest / "type_gate.json")
    return dest


@pytest.fixture
def switch_dir(tmp_path):
    dest = tmp_path / "switches"
    dest.mkdir()
    return dest


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "review.db"


def load_json(path):
    return json.loads(Path(path).read_text())


def economics(price=0.40, dollars=50.0, **overrides):
    contracts = overrides.get("contracts", contracts_from_dollars(dollars, price))
    fee_value = overrides.get("fee", fee(contracts, price))
    cost_value = overrides.get("cost", cost(contracts, price, fee_value))
    return {"price": price, "contracts": contracts, "fee": fee_value, "cost": cost_value}


def samford_entry(**overrides):
    entry = {
        "key": "ncaab:synthetic-samford-unlv:ml:away:None",
        "sport": "ncaab",
        "market": "ml",
        "pick": "Samford Bulldogs ML",
        "home": "UNLV Rebels",
        "away": "Samford Bulldogs",
        "game": "synthetic-samford-unlv",
        "start": "2030-11-07T21:00:00Z",
        "me_type": "marv",
    }
    entry.update(overrides)
    return entry


def team(provider_id, name, alias=None, abbreviation=None):
    body = {"id": provider_id, "name": name}
    if alias is not None:
        body["alias"] = alias
    if abbreviation is not None:
        body["abbreviation"] = abbreviation
    return body


def samford_event(**overrides):
    away = overrides.pop("away_team", team("synthetic-samford", "Samford Bulldogs", "Samford", "SAM"))
    home = overrides.pop("home_team", team("synthetic-unlv", "UNLV Rebels", "UNLV", "UNLV"))
    include_token = overrides.pop("include_token", True)
    long_side = {
        "long": True,
        "description": away["name"],
        "team": away,
    }
    short_side = {
        "long": False,
        "description": home["name"],
        "team": home,
    }
    if include_token:
        long_side["tokenId"] = "synthetic-token-samford"
        short_side["tokenId"] = "synthetic-token-unlv"
    event = {
        "id": "synthetic-samford-unlv-event",
        "slug": "synthetic-cbb-samford-unlv-2030-11-07",
        "league": "cbb",
        "startTime": "2030-11-07T21:00:00Z",
        "closed": False,
        "teams": [away, home],
        "markets": [{
            "slug": "synthetic-samford-unlv-winner",
            "sportsMarketType": "basketball_full_game_winner",
            "closed": False,
            "question": "Synthetic title must not be an identity source",
            "marketSides": [long_side, short_side],
        }],
    }
    event.update(overrides)
    return event


def fill_request(entry=None, event=None, snapshot=None, **overrides):
    entry = samford_entry() if entry is None else entry
    if snapshot is None:
        snapshot = {"events": [samford_event() if event is None else event]}
    body = {
        "entry": entry,
        "snapshot": snapshot,
        "claimed_slug": "synthetic-samford-unlv-winner",
        "claimed_side": "long",
        "claimed_token": "synthetic-token-samford",
        "accounting_date": "2030-11-06",
        "lane": "runner:ml:ncaab:pre",
        "fair": 0.55,
        "edge": 0.10,
    }
    body.update(economics(body.get("price", 0.40)))
    body.update(overrides)
    return body


def exact_request(entry=None, event=None, snapshot=None, **overrides):
    """Request whose fee and cost match the supplied contracts and price."""
    body = fill_request(entry, event, snapshot)
    price = overrides.get("price", body["price"])
    contracts = overrides.get("contracts", contracts_from_dollars(50, price))
    fee_value = overrides.get("fee", fee(contracts, price))
    cost_value = overrides.get("cost", cost(contracts, price, fee_value))
    body.update(overrides)
    body["price"] = price
    body["contracts"] = contracts
    body["fee"] = fee_value
    body["cost"] = cost_value
    return body
