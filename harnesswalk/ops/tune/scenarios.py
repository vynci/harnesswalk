"""Generated scenarios: new entities, constructed expectations, two disjoint splits.

P6 (the scale-up round, docs/tuning.md). The golden sets hold 24 to 28 cases a pack and the
packs' tool tables a handful of rows, so a dataset of thousands of examples and a test set of
a thousand cases need new entities. This module writes them, deterministically from a seed:

- **Entities** (orders, shipments, assets) get ids from a range that belongs to one split:
  `train` and `test` never share an id, and neither uses a golden case's id range. The rows a
  case's tools return are generated with the entity and added to the pack's own tool tables
  for the run (`tools_overlay`), so the harness, policy, approval and audit run unchanged.
- **Expectations** are constructed from the generated rows and the pack's policy (the
  expected tool calls, the approval decision, the facts the reply must state), never taken
  from a model's answer. The node path of each kind is the matching golden case's path.
- **Prompts** come from templates; every kind has one template list per split, so a test
  prompt is never a training prompt even for kinds without an id (knowledge questions, small
  talk). `check_disjoint` verifies it, and the curation drops any example that would break it.
- **Packs**: `ecommerce` and `logistics` have both splits; `device_fleet` is test only (the
  held-out approval-gated pack). Hub codes and knowledge facts are shared by both splits
  (the pack has six hubs and fixed documents); their prompts are not.

A scenario is `{"pack", "split", "kind", "case": <golden case JSON>, "rows": {tool: {key:
row}}}`. The case is in the golden-set format (`ops/eval/golden.py`), so the eval runner scores
it like any other case.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

import yaml

SPLITS = ("train", "test")
TRAIN_PACKS = ("ecommerce", "logistics")
TEST_PACKS = ("ecommerce", "logistics", "device_fleet")
HELD_OUT = ("device_fleet",)
TEST_COUNTS = {"ecommerce": 350, "logistics": 350, "device_fleet": 300}

# id ranges per pack and split (golden ids: QF-20xxx, QT-5812xxxx, TG-604xxx, KW-00xx).
RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "order": {"train": (30000, 59999), "test": (70000, 89999)},
    "tracking": {"train": (60000000, 69999999), "test": (70000000, 79999999)},
    "shipment": {"train": (610000, 699999), "test": (700000, 799999)},
    "asset": {"test": (1000, 9999)},
    "command": {"train": (70000, 79999), "test": (80000, 89999)},
}

# A reply that says an action did not happen (a denied approval, nothing changed).
NOT_DONE = (
    r"(?:\bnot\b|n't|\bnever\b|\bno\b)[^.!?\n]{0,40}\b(?:approv|cancel|refund|rerout|chang|"
    r"sent|go(?:ne)? (?:ahead|through)|proceed|power|clear|delet|purg|happen)"
    r"|\b(?:denied|declined|rejected|refused)\b"
)
NOT_FOUND = (
    r"(?:could ?n[o']t|can ?n[o']t|cannot|unable to|did ?n[o']t|was ?n[o']t able to) "
    r"(?:find|locate)|not (?:be )?found|no (?:record|order|shipment|match)|"
    r"(?:does ?n[o']t|did ?n[o']t|does not) (?:exist|match)|not (?:on file|in our system)"
)


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _ids(rng: random.Random, kind: str, split: str, n: int) -> list[int]:
    lo, hi = RANGES[kind][split]
    return rng.sample(range(lo, hi + 1), n)


OFFLINE = "the device is offline; commands need an online device"


def _hm(rng: random.Random, lo: int, hi: int) -> str:
    """A clock time between `lo` and `hi` o'clock, `HH:MM`."""
    return f"{rng.randint(lo, hi):02d}:{rng.choice(['05', '15', '20', '30', '40', '55'])}"


def _md(date: str) -> str:
    """`2026-10-18` -> `10-18`: matches a full or a yearless date after normalization."""
    return date[5:10]


def relax(path: list[str]) -> list[str]:
    """A golden path without the model calls between two tool calls: a model may call two
    independent tools in one reply (in parallel) or one after the other, and both are right.
    The relaxed path is a subsequence of either."""
    out: list[str] = []
    for i, node in enumerate(path):
        nxt = path[i + 1] if i + 1 < len(path) else None
        if node == "model_layer" and nxt == "tools" and "model_layer" in out:
            continue
        out.append(node)
    return out


def _path_of(pack_dir: Path, case_id: str) -> list[str]:
    for line in (pack_dir / "golden.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            if raw.get("id") == case_id:
                return list((raw.get("expect") or {}).get("path") or [])
    raise KeyError(f"{pack_dir.name} has no golden case {case_id}")


def _golden(pack_dir: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for line in (pack_dir / "golden.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            out[raw["id"]] = raw
    return out


def _alloc(total: int, weights: dict[str, float]) -> dict[str, int]:
    """`total` split over the kinds by weight (largest remainders), every kind at least 1."""
    s = sum(weights.values())
    raw = {k: total * w / s for k, w in weights.items()}
    out = {k: max(1, int(v)) for k, v in raw.items()}
    rest = sorted(weights, key=lambda k: raw[k] - int(raw[k]), reverse=True)
    i = 0
    while sum(out.values()) < total:
        out[rest[i % len(rest)]] += 1
        i += 1
    while sum(out.values()) > total:
        big = max(out, key=out.get)
        out[big] -= 1
    return out


# -- ecommerce (Quillfern Outfitters) ---------------------------------------------------------

PRODUCTS = [
    ("Quillridge 3-person tent", "tent", "289.00"), ("Quillridge 2-person tent", "tent", "219.00"),
    ("Fernglow lantern", "lantern", "42.50"), ("Fernglow lantern, 2 pack", "lanterns", "79.00"),
    ("Emberhollow 5-piece cookware set", "cookware set", "164.00"),
    ("Fernpath 45L backpack", "backpack", "129.00"), ("Fernpath 28L daypack", "daypack", "84.00"),
    ("Mothdown 0C sleeping bag", "sleeping bag", "96.00"),
    ("Mothdown -10C sleeping bag", "winter sleeping bag", "148.00"),
    ("Thistlecrest camp chair", "camp chair", "58.00"),
    ("Brambleway trekking poles", "poles", "67.50"),
    ("Larkspur insulated bottle", "bottle", "31.00"), ("Hollowmere camp stove", "stove", "112.00"),
    ("Saltfen rain shell", "rain jacket", "176.00"),
    ("Driftmoss sleeping mat", "sleeping mat", "73.00"),
    ("Cindervale headlamp", "headlamp", "38.00"), ("Wrenfield hammock", "hammock", "64.00"),
    ("Ashgrove picnic blanket", "blanket", "45.00"),
    ("Pinemarsh water filter", "water filter", "52.00"),
    ("Otterbrook dry bag set", "dry bags", "41.00"),
    ("Kestrel trail shoes", "trail shoes", "139.00"),
    ("Gorseby fleece jacket", "fleece", "92.00"), ("Rookhollow cooler, 24L", "cooler", "118.00"),
    ("Mirefield tarp", "tarp", "49.50"),
]  # fmt: skip
CARRIERS = ["Marlowe Freight", "Brindle Parcel"]
SCANS = [
    ("Harbour District sort hub", "in_transit"),
    ("Northgate parcel depot", "in_transit"),
    ("Out for delivery from Eastbank depot", "out_for_delivery"),
]
DROP = ["front porch", "parcel locker", "front desk", "back door", "mailroom"]

ECOM_T: dict[str, dict[str, list[str]]] = {
    "cancel": {
        "train": [
            "Please cancel order {oid}, I don't need the {item} anymore.",
            "Cancel {oid} for me please.",
            "I'd like to cancel my order {oid}.",
            "Can you cancel {oid}? I ordered it by mistake.",
            "I changed my mind about the {item}. Cancel order {oid}.",
            "Hi, I want to cancel order {oid} before it goes out.",
            "Order {oid} needs to be cancelled, I bought the wrong one.",
            "cancel {oid} pls, wrong colour",
            "Please stop and cancel {oid}. The {item} was meant as a gift and plans changed.",
            "I placed {oid} twice by accident, please cancel that one.",
        ],
        "test": [
            "Could you go ahead and cancel order {oid}? I found the {item} cheaper elsewhere.",
            "I no longer want order {oid}. Please cancel it.",
            "Kindly cancel {oid}; it was a duplicate purchase.",
            "Would you cancel my order {oid} while it is still processing?",
            "Cancel the {item} order, {oid}.",
        ],
    },
    "refund": {
        "train": [
            "I want a refund for order {oid}, the {item} arrived damaged.",
            "Please refund {oid}. The {item} isn't what I expected.",
            "Can I get my money back for order {oid}?",
            "The {item} from {oid} broke on the first trip. Refund please.",
            "Refund order {oid}, it doesn't fit.",
            "I'd like to return {oid} and get a refund.",
            "Order {oid} came with a missing part, I want a refund.",
            "Hello, please process a refund for {oid}.",
        ],
        "test": [
            "Could you issue a refund on {oid}? The {item} has a defect.",
            "I'm not happy with the {item} in order {oid} and would like my money back.",
            "Please give me a refund for my order {oid}.",
            "My {item} ({oid}) arrived scratched. I want a refund.",
            "Refund {oid} to my card, please.",
        ],
    },
    "lookup_then_cancel": {
        "train": [
            "Can you check order {oid} and cancel it if it hasn't shipped yet?",
            "Look up {oid} for me and, if it's still processing, cancel it.",
            "Has {oid} shipped? If not, please cancel it.",
            "Check whether order {oid} is still processing and cancel it if so.",
        ],
        "test": [
            "Is {oid} still being processed? If it is, I want it cancelled.",
            "Please see where order {oid} is and cancel it if it has not left yet.",
            "Before it ships, could you check {oid} and cancel it?",
        ],
    },
    "lookup_then_refund": {
        "train": [
            "Order {oid} arrived last week and the {item} is faulty. Can you check and refund it?",
            "Please look up {oid} and refund it if it's delivered.",
            "Check order {oid} for me; I want a refund for the {item}.",
        ],
        "test": [
            "Could you look at {oid} and, since it was delivered, refund it?",
            "Find order {oid} and process a refund, the {item} is not working.",
        ],
    },
    "lookup": {
        "train": [
            "Where is my order {oid}?",
            "What's the status of {oid}?",
            "Can you check order {oid} for me?",
            "Any update on order {oid}?",
            "Has my {item} order {oid} shipped yet?",
            "Status of {oid} please.",
            "I ordered a {item}, order number {oid}. Where is it?",
            "Could you tell me what's happening with {oid}?",
        ],
        "test": [
            "What is going on with order {oid}?",
            "Please give me an update on {oid}.",
            "Look up {oid} and tell me its status.",
            "Is order {oid} on its way?",
            "I'd like the current status of my {item} order, {oid}.",
        ],
    },
    "track": {
        "train": [
            "Track shipment {tid} for me.",
            "Where is parcel {tid}?",
            "Has {tid} arrived yet?",
            "Can you track {tid}?",
            "What's the latest scan on {tid}?",
            "When will tracking number {tid} be delivered?",
        ],
        "test": [
            "Please give me the tracking status of {tid}.",
            "Where is my package {tid} right now?",
            "Track {tid} and tell me who is carrying it.",
            "Has parcel {tid} been delivered?",
        ],
    },
    "not_found": {
        "train": [
            "Can you check order {oid}?",
            "Where is order {oid}?",
            "Cancel order {oid} please.",
            "What's the status of {oid}?",
        ],
        "test": [
            "Look up my order {oid}.",
            "I need an update on {oid}.",
            "Please find order {oid}.",
        ],
    },
    "multi_lookup": {
        "train": [
            "Where is order {oid}?",
            "Can you check {oid} for me?",
            "What's the status of {oid}?",
        ],
        "test": ["Please look up order {oid}.", "Is {oid} still processing?"],
    },
    "multi_cancel": {
        "train": [
            "OK, please cancel it.",
            "Then cancel it for me.",
            "Cancel it, I don't need it.",
            "Please go ahead and cancel that order.",
        ],
        "test": ["Right, I'd like it cancelled.", "Cancel that one please."],
    },
    "smalltalk": {
        "train": [
            "Thanks, that's all for today!",
            "Hello!",
            "Thank you so much for your help.",
            "Good morning.",
            "Great, cheers.",
            "Bye for now.",
        ],
        "test": ["Hi there.", "Thanks a lot, have a nice day.", "Okay, thank you!"],
    },
}
ECOM_KB = {
    "kb_opened_electronics_window": {
        "train": [
            "How long do I have to return an opened headlamp?",
            "Can opened electronics like a headlamp still be returned after a few weeks?",
            "How many days do I get to return opened electronics?",
        ],
        "test": [
            "Is there a time limit for sending back an opened electronic item?",
            "I opened my headlamp 20 days ago. Can I still return it?",
        ],
    },
    "kb_return_label_fee": {
        "train": [
            "Do you charge for return labels?",
            "What's the fee for a return label?",
            "Is the return label free?",
        ],
        "test": [
            "How much will a prepaid return label cost me?",
            "Will I pay anything for the label if I send something back?",
        ],
    },
    "kb_free_shipping_threshold": {
        "train": [
            "Do you offer free shipping?",
            "When is shipping free?",
            "How much do I need to spend for free delivery?",
        ],
        "test": [
            "What's the minimum order for free shipping?",
            "Is delivery free on bigger orders?",
        ],
    },
    "kb_tent_warranty": {
        "train": [
            "What warranty does a Quillridge tent have?",
            "Is the Quillridge tent under warranty?",
            "How long are Quillridge tents covered?",
        ],
        "test": [
            "How many years of warranty come with the Quillridge tent?",
            "What is the warranty period on Quillridge tents?",
        ],
    },
    "kb_quillridge_tent_weight": {
        "train": [
            "How heavy is the Quillridge 3-person tent packed?",
            "What's the packed weight of the 3-person Quillridge?",
        ],
        "test": [
            "What does the Quillridge 3-person tent weigh in its bag?",
            "Packed weight of the Quillridge three-person tent?",
        ],
    },
    "kb_price_adjustment_window": {
        "train": [
            "If the price drops after I buy, can I get the difference back?",
            "How long after purchase do you do price adjustments?",
            "My backpack went on sale a few days after I bought it. Can I get a price adjustment?",
        ],
        "test": [
            "What is your price adjustment window?",
            "Do you refund the difference if something I bought gets cheaper?",
        ],
    },
}
ECOM_WEIGHTS = {
    "cancel_approve": 3, "cancel_deny": 2, "refund_approve": 2, "refund_deny": 2,
    "lookup_then_cancel_approve": 1, "lookup_then_refund_deny": 1, "conditional_no_cancel": 1,
    "lookup": 2, "track": 1.5, "not_found": 0.5, "kb": 1.5, "smalltalk": 0.5,
    "multi_cancel_approve": 0.6, "multi_cancel_deny": 0.4,
}  # fmt: skip


def _ecom_order(rng: random.Random, oid: int, status: str, tids: list[int]) -> dict[str, Any]:
    name, short, total = rng.choice(PRODUCTS)
    day = rng.randint(1, 28)
    row: dict[str, Any] = {
        "order_id": f"QF-{oid}", "status": status, "items": name, "total": total,
        "placed_on": f"2026-{rng.choice([9, 10]):02d}-{day:02d}",
    }  # fmt: skip
    if status == "processing":
        row["placed_on"] = f"2026-10-{rng.randint(26, 29):02d}"
    if status in ("shipped", "delivered"):
        row["tracking_id"] = f"QT-{tids.pop()}"
    if status == "delivered":  # recent: inside the 30-day window whatever "today" a model assumes
        row["placed_on"] = f"2026-10-{rng.randint(14, 22):02d}"
        row["delivered_on"] = f"2026-10-{int(row['placed_on'][8:]) + rng.randint(3, 6):02d}"
    if status == "shipped":
        row["placed_on"] = f"2026-10-{rng.randint(20, 27):02d}"
    return {"row": row, "short": short}


def _ecom_track_row(rng: random.Random, order: dict[str, Any]) -> dict[str, Any]:
    o = order["row"]
    carrier = rng.choice(CARRIERS)
    if o["status"] == "delivered":
        where = rng.choice(DROP)
        return {
            "tracking_id": o["tracking_id"], "order_id": o["order_id"], "carrier": carrier,
            "last_scan": f"Delivered to {where}, {o['delivered_on']} at {_hm(rng, 8, 18)}",
            "status": "delivered", "eta": o["delivered_on"], "_drop": where,
        }  # fmt: skip
    scan, status = rng.choice(SCANS)
    eta = f"2026-11-{rng.randint(1, 6):02d}"
    return {
        "tracking_id": o["tracking_id"], "order_id": o["order_id"], "carrier": carrier,
        "last_scan": f"{scan}, 2026-10-30 at {_hm(rng, 6, 20)}",
        "status": status, "eta": eta,
    }  # fmt: skip


def ecommerce(split: str, n: int, seed: int, pack_dir: Path) -> list[dict[str, Any]]:
    rng = random.Random(f"ecommerce/{split}/{seed}")
    g = _golden(pack_dir)
    paths = {
        "cancel_approve": g["cancel_approved_qf20430"]["expect"]["path"],
        "cancel_deny": g["cancel_denied_qf20430"]["expect"]["path"],
        "refund_approve": g["refund_approved_qf20388"]["expect"]["path"],
        "refund_deny": g["refund_denied_qf20388"]["expect"]["path"],
        "lookup_then_approve": g["lookup_then_cancel_approved"]["expect"]["path"],
        "lookup_then_deny": g["lookup_then_refund_denied"]["expect"]["path"],
        "one_read": g["order_lookup_qf20417"]["expect"]["path"],
        "no_tool": g["kb_return_label_fee"]["expect"]["path"],
    }
    paths = {k: relax(v) for k, v in paths.items()}
    counts = _alloc(n, ECOM_WEIGHTS)
    oids = _ids(rng, "order", split, n * 2 + 10)
    tids = _ids(rng, "tracking", split, n * 2 + 10)
    out: list[dict[str, Any]] = []
    tpl_i: dict[str, int] = {}

    def tpl(group: str) -> str:
        items = ECOM_T[group][split]
        i = tpl_i.get(group, rng.randrange(len(items)))
        tpl_i[group] = i + 1
        return items[i % len(items)]

    def add(kind: str, case: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
        cid = f"{kind}_{len(out):05d}"
        case = {"id": cid, "title": kind.replace("_", " "), **case}
        case.setdefault("tags", [kind, split, "generated"])
        out.append({"pack": "ecommerce", "split": split, "kind": kind, "case": case,
                    "rows": rows})  # fmt: skip

    def clean(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if not k.startswith("_")}

    for kind, k in counts.items():
        for _ in range(k):
            if kind in ("cancel_approve", "cancel_deny", "lookup_then_cancel_approve",
                        "multi_cancel_approve", "multi_cancel_deny"):  # fmt: skip
                o = _ecom_order(rng, oids.pop(), "processing", tids)
                r = o["row"]
                rows = {
                    "lookup_order": {r["order_id"]: r},
                    "cancel_order": {r["order_id"]: {
                        "order_id": r["order_id"], "status": "cancelled", "refund": r["total"],
                        "refund_to": "original payment method"}},
                }  # fmt: skip
                fill = {"oid": r["order_id"], "item": o["short"]}
                approve = kind.endswith("approve")
                done = {"answer_contains": ["cancel", r["total"]]}
                denied = {"answer_regex": NOT_DONE}
                if kind.startswith("multi"):
                    add(kind, {
                        "turns": [
                            {"user": tpl("multi_lookup").format(**fill), "expect": {
                                "path": paths["one_read"], "tool_calls": ["lookup_order"],
                                "answer_contains": ["processing"]}},
                            {"user": tpl("multi_cancel").format(**fill), "expect": {
                                "path": paths["cancel_approve" if approve else "cancel_deny"],
                                "tool_calls": ["cancel_order"], **(done if approve else denied)}},
                        ],
                        "approval": "approve" if approve else "deny",
                        "expect": {"tool_calls": ["lookup_order", "cancel_order"]},
                        "tags": [kind, split, "generated", "approval", "multi_turn"],
                    }, rows)  # fmt: skip
                    continue
                if kind == "lookup_then_cancel_approve":
                    add(kind, {
                        "prompt": tpl("lookup_then_cancel").format(**fill), "approval": "approve",
                        "expect": {"path": paths["lookup_then_approve"],
                                   "tool_calls": ["lookup_order", "cancel_order"], **done},
                        "tags": [kind, split, "generated", "approval"],
                    }, rows)  # fmt: skip
                    continue
                add(kind, {
                    "prompt": tpl("cancel").format(**fill),
                    "approval": "approve" if approve else "deny",
                    "expect": {"path": paths[kind], "tool_calls": ["cancel_order"],
                               **(done if approve else denied)},
                    "tags": [kind, split, "generated", "approval"],
                }, rows)  # fmt: skip
            elif kind in ("refund_approve", "refund_deny", "lookup_then_refund_deny"):
                o = _ecom_order(rng, oids.pop(), "delivered", tids)
                r = o["row"]
                rows = {
                    "lookup_order": {r["order_id"]: r},
                    "issue_refund": {r["order_id"]: {
                        "order_id": r["order_id"], "refund": r["total"], "status": "refund_issued",
                        "arrives_in": "5 business days"}},
                }  # fmt: skip
                fill = {"oid": r["order_id"], "item": o["short"]}
                approve = kind == "refund_approve"
                expect = (
                    {"answer_contains": [r["total"], "refund"]}
                    if approve
                    else {"answer_regex": NOT_DONE}
                )
                if kind == "lookup_then_refund_deny":
                    add(kind, {
                        "prompt": tpl("lookup_then_refund").format(**fill), "approval": "deny",
                        "expect": {"path": paths["lookup_then_deny"],
                                   "tool_calls": ["lookup_order", "issue_refund"], **expect},
                        "tags": [kind, split, "generated", "approval"],
                    }, rows)  # fmt: skip
                    continue
                add(kind, {
                    "prompt": tpl("refund").format(**fill),
                    "approval": "approve" if approve else "deny",
                    "expect": {"path": paths[kind], "tool_calls": ["issue_refund"], **expect},
                    "tags": [kind, split, "generated", "approval"],
                }, rows)  # fmt: skip
            elif kind == "conditional_no_cancel":
                o = _ecom_order(rng, oids.pop(), "shipped", tids)
                r = o["row"]
                add(kind, {
                    "prompt": tpl("lookup_then_cancel").format(oid=r["order_id"], item=o["short"]),
                    "approval": "approve",
                    "expect": {"path": paths["one_read"], "tool_calls": ["lookup_order"],
                               "answer_contains": ["shipped"]},
                }, {"lookup_order": {r["order_id"]: r}})  # fmt: skip
            elif kind == "lookup":
                status = rng.choice(["processing", "shipped", "delivered", "delivered", "shipped"])
                o = _ecom_order(rng, oids.pop(), status, tids)
                r = o["row"]
                facts = {
                    "processing": ["processing", r["total"]],
                    "shipped": ["shipped", r["total"]],
                    "delivered": ["delivered", _md(r.get("delivered_on", ""))],
                }[status]
                add(kind, {
                    "prompt": tpl("lookup").format(oid=r["order_id"], item=o["short"]),
                    "expect": {"path": paths["one_read"], "tool_calls": ["lookup_order"],
                               "answer_contains": facts},
                }, {"lookup_order": {r["order_id"]: r}})  # fmt: skip
            elif kind == "track":
                o = _ecom_order(rng, oids.pop(), rng.choice(["shipped", "delivered"]), tids)
                t = _ecom_track_row(rng, o)
                facts = (
                    [t["carrier"], t["_drop"]]
                    if t["status"] == "delivered"
                    else [t["carrier"], _md(t["eta"])]
                )
                add(kind, {
                    "prompt": tpl("track").format(tid=t["tracking_id"]),
                    "expect": {"path": paths["one_read"], "tool_calls": ["track_shipment"],
                               "answer_contains": facts},
                }, {"track_shipment": {t["tracking_id"]: clean(t)},
                    "lookup_order": {o["row"]["order_id"]: o["row"]}})  # fmt: skip
            elif kind == "not_found":
                oid = f"QF-{oids.pop()}"
                add(kind, {
                    "prompt": tpl("not_found").format(oid=oid),
                    "expect": {"path": paths["one_read"], "tool_calls": ["lookup_order"],
                               "answer_regex": NOT_FOUND},
                }, {})  # fmt: skip
            elif kind == "kb":
                fact = rng.choice(sorted(ECOM_KB))
                base = g[fact]
                prompts = ECOM_KB[fact][split]
                add(kind, {
                    "prompt": prompts[tpl_i.setdefault(fact, 0) % len(prompts)],
                    "expect": {k: v for k, v in base["expect"].items()},
                    "tags": [kind, split, "generated", "knowledge", fact],
                }, {})  # fmt: skip
                tpl_i[fact] += 1
            elif kind == "smalltalk":
                add(kind, {
                    "prompt": tpl("smalltalk"),
                    "expect": {"path": paths["no_tool"], "tool_calls": []},
                }, {})  # fmt: skip
    return out


# -- logistics (Tallowgate Freight) ------------------------------------------------------------

HUBS = {
    "KRN": ("Kernmoor", "open"), "MAV": ("Mavisgate", "open"), "HLM": ("Holmquay", "congested"),
    "TRB": ("Tarnwick Gate", "open"), "WYN": ("Wyncliff", "closed"), "ORS": ("Orsley Park", "open"),
}  # fmt: skip
OPEN_HUBS = ["KRN", "MAV", "TRB", "ORS"]
BLOCKED_HUBS = ["HLM", "WYN"]
CITIES = [
    "Brackwater",
    "Ashtrave",
    "Saltmarsh",
    "Dunmarrow",
    "Fennick",
    "Larchwhistle",
    "Corvane",
    "Pellam",
    "Hobmere",
    "Quillbury",
    "Stavely Cross",
    "Marrowdene",
]
CONTRACTS = ["CC-17", "CC-22", "CC-31"]
LOG_T: dict[str, dict[str, list[str]]] = {
    "reroute": {
        "train": [
            "Please reroute {sid} through its alternate hub.",
            "{sid} is stuck at {hub_name}. Check its alternate hub and reroute it if possible.",
            "Can you move {sid} to its alternate hub?",
            "Track {sid}, see whether the alternate hub can take it, and reroute it.",
            "{sid} is running late. Reroute it via the alternate hub if that hub can take it.",
            "Reroute {sid} please, it needs to get to {dest} sooner.",
            "Get {sid} moving again: send it through its alternate hub.",
            "Is there a way to move {sid} off {hub_name}? Reroute it if the alternate hub is free.",
        ],
        "test": [
            "Could you divert {sid} to its backup hub if it has room?",
            "{sid} has been sitting at {hub_name} too long. Please reroute it.",
            "I need {sid} rerouted through the alternate hub on its contract.",
            "Check where {sid} is and move it to the alternate hub if you can.",
            "Please send {sid} via its alternate hub instead, {dest} is waiting for it.",
        ],
    },
    "track_eta": {
        "train": [
            "Why is {sid} late and when will it arrive?",
            "Track {sid} and give me its ETA.",
            "Where is {sid} and when will it be delivered?",
            "What's the status of {sid}, and its ETA?",
        ],
        "test": [
            "Where's {sid} right now, and when should it reach {dest}?",
            "Give me the current location and arrival window for {sid}.",
            "Track {sid} for me, and tell me when it is due.",
        ],
    },
    "eta": {
        "train": [
            "What is the ETA for {sid}?",
            "When will {sid} arrive?",
            "ETA on {sid}?",
            "When is {sid} due at {dest}?",
        ],
        "test": ["What arrival window do you have for {sid}?", "When should {sid} get there?"],
    },
    "track": {
        "train": [
            "Where is {sid}?",
            "Track {sid}.",
            "Has {sid} been delivered?",
            "What's the latest on {sid}?",
            "Status of shipment {sid}?",
        ],
        "test": ["Can you tell me where shipment {sid} is?", "Any news on consignment {sid}?"],
    },
    "hub": {
        "train": [
            "How busy is {hub_name} at the moment?",
            "Is {hub} open right now?",
            "What's the cutoff at {hub_name}?",
            "Is the {hub_name} hub taking reroutes?",
        ],
        "test": [
            "What is the current utilisation at {hub_name}?",
            "Can {hub} accept rerouted freight today?",
        ],
    },
    "not_found": {
        "train": ["Where is {sid}?", "Track {sid} please.", "What's the ETA on {sid}?"],
        "test": ["Can you find shipment {sid}?", "Please look up consignment {sid}."],
    },
    "multi_track": {
        "train": ["Where is {sid}?", "Can you track {sid}?", "What's the status of {sid}?"],
        "test": ["Please track consignment {sid}.", "Any update on {sid}?"],
    },
    "multi_reroute": {
        "train": [
            "Can you reroute it through the alternate hub?",
            "Then move it to its alternate hub.",
            "Please reroute it if the alternate hub can take it.",
        ],
        "test": ["Divert it to the backup hub then.", "OK, send it via its alternate hub."],
    },
    "smalltalk": {
        "train": ["Thanks, that's all.", "Hello!", "Cheers for the help.", "Good afternoon."],
        "test": ["Hi.", "Thank you, bye."],
    },
}
LOG_KB = {
    "kb_escalation_time": {
        "train": [
            "At what point is a late consignment escalated?",
            "How late does a shipment have to be before you escalate it?",
        ],
        "test": ["When do you escalate a delayed shipment?"],
    },
    "kb_claim_window": {
        "train": [
            "What's the deadline for a cargo claim?",
            "How long after delivery can I file a claim?",
        ],
        "test": ["By when must a cargo damage claim be lodged?"],
    },
    "kb_cc22_liability": {
        "train": [
            "How much liability per kg does contract CC-22 have?",
            "What is CC-22's liability limit per kilogram?",
        ],
        "test": ["Under CC-22, what is the carrier liable for per kilo?"],
    },
    "kb_hub_cutoff": {
        "train": [
            "When is the daily cutoff at Tarnwick Gate?",
            "What time does Tarnwick Gate stop accepting freight?",
        ],
        "test": ["What's the last drop-off time at the Tarnwick Gate depot?"],
    },
    "kb_dangerous_goods_refused": {
        "train": [
            "Which dangerous goods will you never carry?",
            "What hazardous goods do you refuse?",
        ],
        "test": ["Are there dangerous goods classes you won't accept at all?"],
    },
    "kb_pallet_height": {
        "train": ["How tall can a pallet be?", "What's the pallet height limit?"],
        "test": ["What is the highest a loaded pallet may be?"],
    },
    "kb_peak_surcharge": {
        "train": [
            "What is the peak season surcharge?",
            "When does the peak surcharge apply and how much is it?",
        ],
        "test": ["How big is your peak season surcharge, and when is it charged?"],
    },
}
LOG_KB_FACTS = {  # golden expectations reworded to the fact (the golden phrasing is narrower)
    "kb_escalation_time": ["24 hours"],
    "kb_claim_window": ["21 days"],
    "kb_cc22_liability": ["12.00"],
    "kb_hub_cutoff": ["19:00"],
    "kb_dangerous_goods_refused": ["class 1"],
    "kb_pallet_height": ["1.8"],
    "kb_peak_surcharge": ["4.5"],
}
LOG_WEIGHTS = {
    "reroute_approve": 3, "reroute_deny": 2.5, "reroute_blocked": 1.5, "track_eta": 1.5,
    "eta": 1, "track": 1, "hub": 0.5, "not_found": 0.5, "kb": 1, "smalltalk": 0.4,
    "multi_reroute_approve": 0.6, "multi_reroute_deny": 0.5,
}  # fmt: skip


def _shipment(rng: random.Random, sid: int, alt: str) -> dict[str, Any]:
    cur = rng.choice([h for h in HUBS if h != alt])
    origin, dest = rng.sample(CITIES, 2)
    status = rng.choice(["in_transit", "delayed", "held", "delayed"])
    day = rng.randint(12, 15)
    event = {
        "in_transit": f"Scanned out of {cur} on 2026-10-{day} at {_hm(rng, 5, 20)}",
        "delayed": f"Waiting at {cur} since 2026-10-{day} at {_hm(rng, 5, 22)}",
        "held": f"Held at {cur} on 2026-10-{day} at {rng.randint(6, 18):02d}:00",
    }[status]  # fmt: skip
    return {
        "shipment_id": f"TG-{sid}", "status": status, "current_hub": cur, "origin": origin,
        "destination": dest, "contract": rng.choice(CONTRACTS), "alternate_hub": alt,
        "last_event": event,
    }  # fmt: skip


def _window(rng: random.Random, lo: int, hi: int) -> tuple[str, str, str]:
    day = f"2026-10-{rng.randint(lo, hi):02d}"
    start = rng.choice([8, 9, 10, 12, 13, 14, 16])
    return day, f"{start:02d}:00", f"{start + 4:02d}:00"


def logistics(split: str, n: int, seed: int, pack_dir: Path) -> list[dict[str, Any]]:
    rng = random.Random(f"logistics/{split}/{seed}")
    g = _golden(pack_dir)
    paths = {
        "reroute_approve": g["reroute_three_tools"]["expect"]["path"],
        "reroute_deny": g["reroute_denied_held"]["expect"]["path"],
        "two_reads": g["track_then_eta_in_transit"]["expect"]["path"],
        "one_read": g["eta_single"]["expect"]["path"],
        "no_tool": g["kb_claim_window"]["expect"]["path"],
    }
    paths = {k: relax(v) for k, v in paths.items()}
    counts = _alloc(n, LOG_WEIGHTS)
    sids = _ids(rng, "shipment", split, n + 10)
    out: list[dict[str, Any]] = []
    tpl_i: dict[str, int] = {}

    def tpl(group: str) -> str:
        items = LOG_T[group][split]
        i = tpl_i.get(group, rng.randrange(len(items)))
        tpl_i[group] = i + 1
        return items[i % len(items)]

    def add(kind: str, case: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
        cid = f"{kind}_{len(out):05d}"
        case = {"id": cid, "title": kind.replace("_", " "), **case}
        case.setdefault("tags", [kind, split, "generated"])
        out.append({"pack": "logistics", "split": split, "kind": kind, "case": case,
                    "rows": rows})  # fmt: skip

    for kind, k in counts.items():
        for _ in range(k):
            if kind in ("reroute_approve", "reroute_deny", "multi_reroute_approve",
                        "multi_reroute_deny"):  # fmt: skip
                alt = rng.choice(OPEN_HUBS)
                s = _shipment(rng, sids.pop(), alt)
                day, a, b = _window(rng, 16, 21)
                eday, ea, eb = _window(rng, 17, 23)
                rows = {
                    "track": {s["shipment_id"]: s},
                    "eta": {s["shipment_id"]: {"shipment_id": s["shipment_id"],
                            "eta": f"{eday} between {ea} and {eb}", "confidence": "low",
                            "delay_reason": f"held_at_{s['current_hub']}"}},
                    "reroute": {s["shipment_id"]: {"shipment_id": s["shipment_id"],
                                "status": "rerouted", "via": alt,
                                "new_eta": f"{day} between {a} and {b}"}},
                }  # fmt: skip
                fill = {"sid": s["shipment_id"], "hub_name": HUBS[s["current_hub"]][0],
                        "dest": s["destination"]}  # fmt: skip
                approve = kind.endswith("approve")
                done = {"answer_contains": [alt, _md(day), a]}
                denied = {"answer_regex": NOT_DONE}
                if kind.startswith("multi"):
                    add(kind, {
                        "turns": [
                            {"user": tpl("multi_track").format(**fill), "expect": {
                                "path": paths["one_read"], "tool_calls": ["track"],
                                "answer_contains": [s["current_hub"]]}},
                            {"user": tpl("multi_reroute").format(**fill), "expect": {
                                "tool_calls": ["hub_capacity", "reroute"],
                                **(done if approve else denied)}},
                        ],
                        "approval": "approve" if approve else "deny",
                        "expect": {"tool_calls": ["track", "hub_capacity", "reroute"]},
                        "tags": [kind, split, "generated", "approval", "multi_turn"],
                    }, rows)  # fmt: skip
                    continue
                add(kind, {
                    "prompt": tpl("reroute").format(**fill),
                    "approval": "approve" if approve else "deny",
                    "expect": {"path": paths[kind],
                               "tool_calls": ["track", "hub_capacity", "reroute"],
                               **(done if approve else denied)},
                    "tags": [kind, split, "generated", "approval"],
                }, rows)  # fmt: skip
            elif kind == "reroute_blocked":
                alt = rng.choice(BLOCKED_HUBS)
                s = _shipment(rng, sids.pop(), alt)
                fill = {"sid": s["shipment_id"], "hub_name": HUBS[s["current_hub"]][0],
                        "dest": s["destination"]}  # fmt: skip
                add(kind, {
                    "prompt": tpl("reroute").format(**fill), "approval": "approve",
                    "expect": {"path": paths["two_reads"], "tool_calls": ["track", "hub_capacity"],
                               "answer_contains": [alt, HUBS[alt][1]]},
                }, {"track": {s["shipment_id"]: s}})  # fmt: skip
            elif kind in ("track_eta", "eta"):
                s = _shipment(rng, sids.pop(), rng.choice(list(HUBS)))
                day, a, b = _window(rng, 15, 22)
                conf = rng.choice(["high", "medium", "low"])
                rows = {
                    "track": {s["shipment_id"]: s},
                    "eta": {s["shipment_id"]: {"shipment_id": s["shipment_id"],
                            "eta": f"{day} between {a} and {b}", "confidence": conf,
                            "delay_reason": "none" if s["status"] == "in_transit"
                            else rng.choice(["berth_queue", "linehaul_driver_shortage",
                                             "yard_closure", "weather"])}},
                }  # fmt: skip
                fill = {"sid": s["shipment_id"], "dest": s["destination"]}
                add(kind, {
                    "prompt": tpl(kind).format(**fill),
                    "expect": {"path": paths["two_reads" if kind == "track_eta" else "one_read"],
                               "tool_calls": ["track", "eta"] if kind == "track_eta" else ["eta"],
                               "answer_contains": [_md(day), a, conf]},
                }, rows)  # fmt: skip
            elif kind == "track":
                s = _shipment(rng, sids.pop(), rng.choice(list(HUBS)))
                add(kind, {
                    "prompt": tpl("track").format(sid=s["shipment_id"], dest=s["destination"]),
                    "expect": {"path": paths["one_read"], "tool_calls": ["track"],
                               "answer_contains": [s["current_hub"]]},
                }, {"track": {s["shipment_id"]: s}})  # fmt: skip
            elif kind == "hub":
                hub = rng.choice(list(HUBS))  # six fixed hubs: the answer is the tool call
                add(kind, {
                    "prompt": tpl("hub").format(hub=hub, hub_name=HUBS[hub][0]),
                    "expect": {"path": paths["one_read"], "tool_calls": ["hub_capacity"]},
                }, {})  # fmt: skip
            elif kind == "not_found":
                sid = f"TG-{sids.pop()}"
                add(kind, {
                    "prompt": tpl("not_found").format(sid=sid),
                    "expect": {"path": paths["one_read"], "answer_regex": NOT_FOUND},
                }, {})  # fmt: skip
            elif kind == "kb":
                fact = rng.choice(sorted(LOG_KB))
                base = g[fact]["expect"]
                prompts = LOG_KB[fact][split]
                add(kind, {
                    "prompt": prompts[tpl_i.setdefault(fact, 0) % len(prompts)],
                    "expect": {"path": base["path"], "tool_calls": [],
                               "citations": base["citations"],
                               "answer_contains": LOG_KB_FACTS[fact]},
                    "tags": [kind, split, "generated", "knowledge", fact],
                }, {})  # fmt: skip
                tpl_i[fact] += 1
            elif kind == "smalltalk":
                add(kind, {
                    "prompt": tpl("smalltalk"),
                    "expect": {"path": paths["no_tool"], "tool_calls": []},
                }, {})  # fmt: skip
    return out


# -- device_fleet (Kestrelway Field Services, test only) ---------------------------------------

ASSET_TYPES = [
    ("Shop Pickup", "pickup"),
    ("Route Van", "van"),
    ("City Hauler", "box_truck"),
    ("Linehaul Tractor", "tractor"),
    ("Reefer", "reefer"),
    ("Service Van", "van"),
]
DEV_T = {
    "power_off": [
        "Power off {name} ({aid}).",
        "Please shut down the device on {name}, {aid}, fully.",
        "Turn {aid} off completely, the workshop needs it down.",
        "I need {name} ({aid}) powered off for a wiring job.",
    ],
    "clear_sms": [
        "Clear the SMS inbox on {name} ({aid}).",
        "Delete all the text messages on {aid}.",
        "Wipe the modem's SMS messages on {name}, {aid}.",
    ],
    "purge": [
        "Purge all data from {name}, {aid}.",
        "Permanently delete the telemetry and media of {aid}.",
        "{name} ({aid}) is being sold. Purge its data.",
    ],
    "reboot": [
        "Reboot {name} ({aid}), its GPS fix is stuck.",
        "Please restart the device on {aid}.",
        "Reboot {aid}.",
    ],
    "obd": [
        "Run an OBD scan on {name} ({aid}).",
        "Can you read the OBD data from {aid} now?",
    ],
    "status": [
        "Is {name} ({aid}) online, and when was it last seen?",
        "What's the device status of {aid}?",
        "When did {name} ({aid}) last check in?",
    ],
    "multi_status": ["Is {name} ({aid}) online right now?", "Check the status of {aid} for me."],
    "multi_power_off": ["Then power it off.", "OK, shut it down fully."],
}
DEV_WEIGHTS = {
    "power_off_approve": 2, "power_off_deny": 2, "power_off_offline": 0.8, "clear_sms_approve": 1.5,
    "clear_sms_deny": 1.5, "purge_approve": 1, "purge_deny": 1.5, "reboot_admin": 1.2,
    "reboot_operator": 0.8, "obd_scan": 0.8, "status": 1.2, "multi_power_off_approve": 0.5,
    "multi_power_off_deny": 0.5,
}  # fmt: skip


def device_fleet(split: str, n: int, seed: int, pack_dir: Path) -> list[dict[str, Any]]:
    if split != "test":
        raise ValueError("device_fleet is held out: it has a test split only")
    rng = random.Random(f"device_fleet/{split}/{seed}")
    g = _golden(pack_dir)
    paths = {
        "approve": g["power_off_approved"]["expect"]["path"],
        "deny": g["power_off_denied"]["expect"]["path"],
        "write": g["admin_reboot_queued"]["expect"]["path"],
        "role_denied": g["operator_reboot_denied"]["expect"]["path"],
        "obd": g["operator_obd_scan"]["expect"]["path"],
        "one_read": g["offline_asset_status"]["expect"]["path"],
    }
    paths = {k: relax(v) for k, v in paths.items()}
    counts = _alloc(n, DEV_WEIGHTS)
    aids = _ids(rng, "asset", split, 2 * n + 10)
    cmds = _ids(rng, "command", split, n * 2 + 10)
    out: list[dict[str, Any]] = []
    tpl_i: dict[str, int] = {}

    def tpl(group: str) -> str:
        items = DEV_T[group]
        i = tpl_i.get(group, rng.randrange(len(items)))
        tpl_i[group] = i + 1
        return items[i % len(items)]

    def asset(online: bool = True) -> dict[str, Any]:
        num = aids.pop()
        label, typ = rng.choice(ASSET_TYPES)
        aid = f"KW-{num}"
        seen = (
            f"2026-10-23T08:{rng.randint(10, 59):02d}:{rng.randint(0, 59):02d}Z"
            if online
            else f"2026-10-{rng.randint(17, 21)}T{_hm(rng, 6, 20)}:{rng.randint(0, 59):02d}Z"
        )
        return {
            "asset_id": aid, "name": f"{label} {num % 100 or 1}", "online": online,
            "last_seen": seen, "agent_version": "0.9.4", "led_state": "green_solid" if online
            else "unknown", "health_flags": [] if online else ["under_voltage"], "rate_hz": 10,
            "obd_tier": rng.choice(["full", "basic"]), "type": typ,
        }  # fmt: skip

    def status_row(a: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in a.items() if k != "type"}

    def add(kind: str, case: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
        cid = f"{kind}_{len(out):05d}"
        case = {"id": cid, "title": kind.replace("_", " "), **case}
        case.setdefault("tags", [kind, split, "generated"])
        out.append({"pack": "device_fleet", "split": split, "kind": kind, "case": case,
                    "rows": rows})  # fmt: skip

    for kind, k in counts.items():
        for _ in range(k):
            online = kind != "power_off_offline"
            a = asset(online)
            fill = {"aid": a["asset_id"], "name": a["name"]}
            cmd = f"CMD-{cmds.pop()}"
            base_rows = {"get_asset_status": {a["asset_id"]: status_row(a)}}
            denied = {"answer_regex": NOT_DONE}
            if kind.startswith("power_off") or kind.startswith("multi_power_off"):
                row = (
                    {"asset_id": a["asset_id"], "command": "power_off", "status": "queued",
                     "request_id": cmd, "ttl_s": 60, "warning": "not remotely recoverable:"
                     " someone must restore power at the vehicle"}
                    if online else
                    {"asset_id": a["asset_id"], "command": "power_off", "error": "device_offline",
                     "status": 409, "message": OFFLINE}
                )  # fmt: skip
                rows = {**base_rows, "power_off_device": {a["asset_id"]: row}}
                approve = kind.endswith("approve") or kind == "power_off_offline"
                done = {"answer_contains": [cmd]} if online else {"answer_contains": ["offline"]}
                if kind.startswith("multi"):
                    add(kind, {
                        "turns": [
                            {"user": tpl("multi_status").format(**fill), "expect": {
                                "path": paths["one_read"], "tool_calls": ["get_asset_status"],
                                "answer_contains": ["online"]}},
                            {"user": tpl("multi_power_off").format(**fill), "expect": {
                                "path": paths["approve" if approve else "deny"],
                                "tool_calls": ["power_off_device"],
                                **(done if approve else denied)}},
                        ],
                        "approval": "approve" if approve else "deny",
                        "expect": {"tool_calls": ["get_asset_status", "power_off_device"]},
                        "tags": [kind, split, "generated", "approval", "multi_turn"],
                    }, rows)  # fmt: skip
                    continue
                add(kind, {
                    "prompt": tpl("power_off").format(**fill),
                    "approval": "approve" if approve else "deny",
                    "expect": {"path": paths["approve" if approve else "deny"],
                               "tool_calls": ["power_off_device"], **(done if approve else denied)},
                    "tags": [kind, split, "generated", "approval"],
                }, rows)  # fmt: skip
            elif kind.startswith("clear_sms"):
                count = rng.randint(1, 9)
                sr = f"SR-{cmds.pop() % 10000:04d}"
                rows = {**base_rows,
                        "get_inbox": {a["asset_id"]: {"asset_id": a["asset_id"], "messages": count,
                                                      "read_id": sr}},
                        "clear_sms": {a["asset_id"]: {"asset_id": a["asset_id"], "action": "clear",
                                      "status": "pending", "read_id": sr,
                                      "messages_to_clear": count,
                                      "irreversible": True}}}  # fmt: skip
                approve = kind.endswith("approve")
                add(kind, {
                    "prompt": tpl("clear_sms").format(**fill),
                    "approval": "approve" if approve else "deny",
                    "expect": {"path": paths["approve" if approve else "deny"],
                               "tool_calls": ["clear_sms"],
                               **({"answer_contains": [sr]} if approve else denied)},
                    "tags": [kind, split, "generated", "approval"],
                }, rows)  # fmt: skip
            elif kind.startswith("purge"):
                pg = f"PG-{cmds.pop() % 10000:04d}"
                rows = {**base_rows, "purge_device": {a["asset_id"]: {
                    "asset_id": a["asset_id"], "purge_id": pg, "status": "queued",
                    "scopes": ["workflows", "telemetry", "media"],
                    "preview": {"workflow_runs": rng.randint(20, 400),
                                "telemetry_sessions": rng.randint(5, 60),
                                "media_sessions": rng.randint(0, 12)}}}}  # fmt: skip
                approve = kind.endswith("approve")
                add(kind, {
                    "prompt": tpl("purge").format(**fill),
                    "approval": "approve" if approve else "deny",
                    "expect": {"path": paths["approve" if approve else "deny"],
                               "tool_calls": ["purge_device"],
                               **({"answer_contains": [pg]} if approve else denied)},
                    "tags": [kind, split, "generated", "approval"],
                }, rows)  # fmt: skip
            elif kind.startswith("reboot"):
                rows = {**base_rows, "reboot_device": {a["asset_id"]: {
                    "asset_id": a["asset_id"], "command": "reboot", "status": "queued",
                    "request_id": cmd, "ttl_s": 60, "expected_back_s": 90}}}  # fmt: skip
                if kind == "reboot_admin":
                    add(kind, {
                        "prompt": tpl("reboot").format(**fill),
                        "expect": {"path": paths["write"], "tool_calls": ["reboot_device"],
                                   "answer_contains": [cmd, "90"]},
                    }, rows)  # fmt: skip
                else:
                    add(kind, {
                        "prompt": tpl("reboot").format(**fill), "roles": ["operator"],
                        "expect": {"path": paths["role_denied"], "tool_calls": ["reboot_device"],
                                   "answer_regex": r"admin"},
                        "tags": [kind, split, "generated", "roles"],
                    }, rows)  # fmt: skip
            elif kind == "obd_scan":
                a["obd_tier"] = "full"
                rows = {**base_rows, "obd_scan": {a["asset_id"]: {
                    "asset_id": a["asset_id"], "command": "obd_scan", "status": "accepted",
                    "request_id": cmd, "ttl_s": 60, "result_after_s": 20}}}  # fmt: skip
                add(kind, {
                    "prompt": tpl("obd").format(**fill), "roles": ["operator"],
                    "expect": {"path": paths["obd"], "tool_calls": ["obd_scan"],
                               "answer_contains": [cmd]},
                    "tags": [kind, split, "generated", "roles"],
                }, rows)  # fmt: skip
            elif kind == "status":
                if rng.random() < 0.5:
                    a = asset(False)
                    fill = {"aid": a["asset_id"], "name": a["name"]}
                    base_rows = {"get_asset_status": {a["asset_id"]: status_row(a)}}
                seen = a["last_seen"]
                add(kind, {
                    "prompt": tpl("status").format(**fill),
                    "expect": {"path": paths["one_read"], "tool_calls": ["get_asset_status"],
                               "answer_contains": ["online" if a["online"] else "offline",
                                                   _md(seen[:10]), seen[11:16]]},
                }, base_rows)  # fmt: skip
    return out


GENERATORS = {"ecommerce": ecommerce, "logistics": logistics, "device_fleet": device_fleet}


def generate(pack: str, split: str, n: int, seed: int, datasets: Path) -> list[dict[str, Any]]:
    """`n` scenarios of one pack and split, deterministic in (pack, split, n, seed)."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if split == "train" and pack not in TRAIN_PACKS:
        raise ValueError(f"{pack} has no training split (held out or not generated)")
    out = GENERATORS[pack](split, n, seed, datasets / pack)
    rng = random.Random(f"order/{pack}/{split}/{seed}")
    rng.shuffle(out)  # a prefix (a budget stop) keeps the kind mix
    return out


# -- tables -----------------------------------------------------------------------------------


def tools_overlay(tools_text: str, rows: list[dict[str, dict[str, Any]]]) -> str:
    """The pack's tools.yaml with these generated rows added to the tool tables."""
    tools = yaml.safe_load(tools_text) or []
    by_name = {t["name"]: t for t in tools if isinstance(t, dict) and "name" in t}
    for item in rows:
        for tool, table in (item or {}).items():
            spec = by_name.get(tool)
            if spec is None:
                raise KeyError(f"no tool {tool!r} in this pack")
            spec.setdefault("table", {}).update(table)
    return yaml.safe_dump(tools, sort_keys=False, allow_unicode=True, width=1000)


# -- disjointness -----------------------------------------------------------------------------

ID_PATTERN = re.compile(r"\b(?:QF|QT|TG|KW|CMD|SR|PG)-\d{4,8}\b")


def prompts_of(scenarios: list[dict[str, Any]]) -> set[str]:
    out = set()
    for s in scenarios:
        case = s["case"]
        texts = [t["user"] for t in case.get("turns") or []] or [case.get("prompt") or ""]
        out |= {_norm(t) for t in texts if t}
    return out


def entity_ids(scenarios: list[dict[str, Any]]) -> set[str]:
    """Every generated entity id the scenarios' prompts and rows carry."""
    out = set()
    for s in scenarios:
        out |= set(ID_PATTERN.findall(json.dumps(s["rows"]) + json.dumps(s["case"])))
    return out


def check_disjoint(train: list[dict[str, Any]], test: list[dict[str, Any]]) -> dict[str, Any]:
    """Shared prompts and shared entity ids of two scenario lists (both must be empty)."""
    prompts = prompts_of(train) & prompts_of(test)
    ids = entity_ids(train) & entity_ids(test)
    return {"shared_prompts": sorted(prompts)[:20], "n_shared_prompts": len(prompts),
            "shared_ids": sorted(ids)[:20], "n_shared_ids": len(ids)}  # fmt: skip


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
    )
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
