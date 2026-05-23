"""Build-level cart optimization across retailers."""

from __future__ import annotations

import html as html_module
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations


@dataclass
class CartLine:
    name: str
    store: str
    price: float
    url: str
    in_stock: bool | None


@dataclass
class BuildPlan:
    lines: list[CartLine] = field(default_factory=list)
    total: float = 0.0
    vendor_count: int = 0
    label: str = ""
    missing_parts: list[str] = field(default_factory=list)

    @property
    def vendors(self) -> set[str]:
        return {line.store for line in lines}


def _latest_offer(entry: dict, history: dict) -> tuple[float | None, bool | None, str]:
    records = history.get(entry["url"], [])
    latest = records[-1] if records else None
    if not latest:
        return None, None, entry["url"]
    return latest.get("price"), latest.get("in_stock"), entry["url"]


def _offers_for_groups(
    groups: dict[str, list[dict]],
    history: dict,
    *,
    in_stock_only: bool = True,
) -> dict[str, list[tuple[str, float, str, bool | None]]]:
    offers: dict[str, list[tuple[str, float, str, bool | None]]] = {}
    for name, entries in groups.items():
        part_offers = []
        for entry in entries:
            price, in_stock, url = _latest_offer(entry, history)
            if price is None:
                continue
            if in_stock_only and in_stock is False:
                continue
            part_offers.append((entry["store"].lower(), price, url, in_stock))
        offers[name] = part_offers
    return offers


def cheapest_cart(
    groups: dict[str, list[dict]],
    history: dict,
    *,
    in_stock_only: bool = True,
) -> BuildPlan:
    offers = _offers_for_groups(groups, history, in_stock_only=in_stock_only)
    plan = BuildPlan(label="Cheapest mix")
    for name, part_offers in offers.items():
        if not part_offers:
            plan.missing_parts.append(name)
            continue
        store, price, url, in_stock = min(part_offers, key=lambda o: o[1])
        plan.lines.append(CartLine(name=name, store=store, price=price, url=url, in_stock=in_stock))
        plan.total += price
    plan.vendor_count = len(plan.vendors)
    return plan


def single_vendor_cart(
    store: str,
    groups: dict[str, list[dict]],
    history: dict,
    *,
    in_stock_only: bool = True,
) -> BuildPlan:
    offers = _offers_for_groups(groups, history, in_stock_only=in_stock_only)
    plan = BuildPlan(label=f"All from {store.title()}")
    for name, part_offers in offers.items():
        match = next((o for o in part_offers if o[0] == store), None)
        if not match:
            plan.missing_parts.append(name)
            continue
        _, price, url, in_stock = match
        plan.lines.append(CartLine(name=name, store=store, price=price, url=url, in_stock=in_stock))
        plan.total += price
    plan.vendor_count = 1 if plan.lines and not plan.missing_parts else 0
    return plan


def best_single_vendor_carts(
    groups: dict[str, list[dict]],
    history: dict,
    *,
    in_stock_only: bool = True,
) -> list[BuildPlan]:
    all_stores: set[str] = set()
    for entries in groups.values():
        for e in entries:
            all_stores.add(e["store"].lower())

    complete: list[BuildPlan] = []
    part_count = len(groups)
    for store in all_stores:
        plan = single_vendor_cart(store, groups, history, in_stock_only=in_stock_only)
        if len(plan.lines) == part_count and not plan.missing_parts:
            complete.append(plan)
    complete.sort(key=lambda p: p.total)
    return complete


def cheapest_cart_max_vendors(
    groups: dict[str, list[dict]],
    history: dict,
    max_vendors: int,
    *,
    in_stock_only: bool = True,
) -> BuildPlan | None:
    offers = _offers_for_groups(groups, history, in_stock_only=in_stock_only)
    all_stores = sorted({o[0] for part in offers.values() for o in part})
    part_names = list(groups.keys())

    best: BuildPlan | None = None

    for k in range(1, min(max_vendors, len(all_stores)) + 1):
        for store_subset in combinations(all_stores, k):
            store_set = set(store_subset)
            total = 0.0
            lines: list[CartLine] = []
            missing: list[str] = []
            for name in part_names:
                part_offers = [o for o in offers.get(name, []) if o[0] in store_set]
                if not part_offers:
                    missing.append(name)
                    break
                store, price, url, in_stock = min(part_offers, key=lambda o: o[1])
                lines.append(CartLine(name=name, store=store, price=price, url=url, in_stock=in_stock))
                total += price
            else:
                if missing:
                    continue
                plan = BuildPlan(
                    lines=lines,
                    total=total,
                    vendor_count=len(store_set),
                    label=f"Cheapest ≤{max_vendors} vendors",
                )
                if best is None or plan.total < best.total:
                    best = plan
    return best


def group_products(products: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        groups[p["name"]].append(p)
    return dict(groups)


def _plan_table(plan: BuildPlan) -> str:
    if not plan.lines:
        return "<p style='color:#888;font-size:13px'>No priced parts available yet.</p>"

    rows = ""
    for line in plan.lines:
        short_name = line.name if len(line.name) <= 48 else line.name[:45] + "..."
        rows += (
            f"<tr>"
            f"<td style='padding:6px 8px;border:1px solid #ddd'>{html_module.escape(short_name)}</td>"
            f"<td style='padding:6px 8px;border:1px solid #ddd'>{line.store.title()}</td>"
            f"<td style='padding:6px 8px;border:1px solid #ddd;text-align:right'>"
            f"<a href='{html_module.escape(line.url)}' target='_blank'>${line.price:.2f}</a></td>"
            f"</tr>"
        )
    missing_note = ""
    if plan.missing_parts:
        missing_note = (
            f"<p style='color:#c0392b;font-size:12px;margin:8px 0 0'>"
            f"Missing prices for {len(plan.missing_parts)} part(s).</p>"
        )
    return (
        f"<table style='border-collapse:collapse;width:100%;font-size:13px;margin-top:8px'>"
        f"<thead><tr style='background:#f2f2f2'>"
        f"<th style='padding:6px 8px;border:1px solid #ddd;text-align:left'>Part</th>"
        f"<th style='padding:6px 8px;border:1px solid #ddd;text-align:left'>Store</th>"
        f"<th style='padding:6px 8px;border:1px solid #ddd;text-align:right'>Price</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>{missing_note}"
    )


def render_build_summary_html(products: list[dict], history: dict) -> str:
    """HTML card comparing cheapest mix vs single-vendor options."""
    groups = group_products(products)
    if not groups:
        return ""

    mix = cheapest_cart(groups, history)
    singles = best_single_vendor_carts(groups, history)
    two_vendor = cheapest_cart_max_vendors(groups, history, 2)

    def _card(title: str, plan: BuildPlan, note: str = "") -> str:
        vendor_text = (
            f"{plan.vendor_count} vendor{'s' if plan.vendor_count != 1 else ''}"
            if plan.vendor_count
            else "—"
        )
        return (
            f"<div style='flex:1;min-width:260px;background:#fafafa;border:1px solid #ddd;"
            f"border-radius:6px;padding:14px'>"
            f"<h3 style='margin:0 0 6px;font-size:15px'>{html_module.escape(title)}</h3>"
            f"<p style='margin:0;font-size:22px;font-weight:bold;color:#2c3e50'>"
            f"${plan.total:.2f}</p>"
            f"<p style='margin:4px 0 0;font-size:12px;color:#666'>{vendor_text}"
            f"{(' · ' + note) if note else ''}</p>"
            f"{_plan_table(plan)}"
            f"</div>"
        )

    cards = [_card("Cheapest mix (recommended)", mix)]
    if singles:
        best_single = singles[0]
        delta = best_single.total - mix.total
        note = f"+${delta:.2f} vs cheapest mix" if delta > 0 else "same as cheapest mix"
        store_name = next(iter(best_single.vendors)).title()
        cards.append(_card(f"Single vendor — {store_name}", best_single, note))
    if two_vendor and (not singles or two_vendor.total < singles[0].total or two_vendor.vendor_count < singles[0].vendor_count):
        delta = two_vendor.total - mix.total
        note = f"+${delta:.2f} vs cheapest mix · max 2 checkouts"
        cards.append(_card("Cheapest ≤2 vendors", two_vendor, note))

    return (
        f"<section style='margin-bottom:24px'>"
        f"<h2 style='font-size:17px;margin:0 0 12px'>Build summary</h2>"
        f"<div style='display:flex;flex-wrap:wrap;gap:16px'>{''.join(cards)}</div>"
        f"<p style='font-size:12px;color:#888;margin:12px 0 0'>"
        f"Uses latest in-stock prices where known. Out-of-stock offers excluded.</p>"
        f"</section>"
    )
