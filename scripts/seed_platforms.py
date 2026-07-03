#!/usr/bin/env python3
"""
Seed all scraper platforms into the Platform table so they appear as wallet
cards in Dashboard and Portfolio, even if they have no bets yet.
"""
import sys, os
sys.path.insert(0, "/app")

# Bootstrap DB
from src.database.database import db, Platform as PlatformRow, Wallet as WalletRow

PLATFORMS = [
    # slug               display_name         ptype         url
    ("pinnacle",        "Pinnacle",          "bookmaker",  "https://pinnacle.com"),
    ("unibet",          "Unibet",            "bookmaker",  "https://unibet.es"),
    ("comeon",          "ComeOn",            "bookmaker",  "https://comeon.com"),
    ("stake",           "Stake",             "bookmaker",  "https://stake.com"),
    ("betsson",         "Betsson",           "bookmaker",  "https://betsson.com"),
    ("nordicbet",       "NordicBet",         "bookmaker",  "https://nordicbet.com"),
    ("coolbet",         "Coolbet",           "bookmaker",  "https://coolbet.com"),
    ("marathonbet",     "Marathonbet",       "bookmaker",  "https://marathonbet.com"),
    ("1xbet",           "1xBet",             "bookmaker",  "https://1xbet.com"),
    ("winamax",         "Winamax",           "bookmaker",  "https://winamax.es"),
    ("betano",          "Betano",            "bookmaker",  "https://betano.com"),
    ("smarkets",        "Smarkets",          "exchange",   "https://smarkets.com"),
    ("xbet",            "Xbet",              "bookmaker",  "https://x.bet"),
    ("polymarket",      "Polymarket",        "prediction_market", "https://polymarket.com"),
    ("kalshi",          "Kalshi",            "prediction_market", "https://kalshi.com"),
    ("manifold",        "Manifold",          "prediction_market", "https://manifold.markets"),
]

created = 0
for slug, name, ptype, url in PLATFORMS:
    row, was_created = PlatformRow.get_or_create(
        slug=slug,
        defaults={"name": name, "ptype": ptype, "url": url, "currency": "EUR", "is_active": True}
    )
    if was_created:
        print(f"  + Created platform: {slug}")
        created += 1
    else:
        # Update display name/type if blank
        changed = False
        if not row.name:
            row.name = name; changed = True
        if not row.ptype:
            row.ptype = ptype; changed = True
        if changed:
            row.save()
        print(f"  . Exists: {slug}")

    # Ensure a Wallet row exists too (€0 balance so it shows up as a card)
    wallet_exists = WalletRow.get_or_none(WalletRow.platform == slug)
    if wallet_exists is None:
        WalletRow.create(
            platform=slug,
            initial_balance=0.0,
            balance=0.0,
            equity=0.0,
            deposits=0.0,
            withdrawals=0.0,
        )
        print(f"    + Created wallet row for {slug}")

print(f"\nDone. {created} new platform(s) created.")
