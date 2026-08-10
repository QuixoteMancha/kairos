"""Reparación de una regresión real causada por correr `fix_player_
canonical_name_te_order.py --apply` INMEDIATAMENTE después de
`merge_duplicate_players.py --apply` (2026-08-10, mismo incidente que
`merge_duplicate_players_symmetric_bug`, consecuencia directa de la
reparación de ese bug).

Causa real: tras fusionar 2 `Player` duplicados (uno TE-nativo, uno ya
correcto), el sobreviviente queda con partidos de AMBAS variantes de
orden en su texto crudo (`MatchHistory.player_a_name`/`player_b_name`).
El chequeo fail-closed de `find_reorder_candidates()` ("el
canonical_name actual coincide con ALGÚN texto crudo visto") se cumple
incluso para un `canonical_name` YA CORRECTO, porque el texto crudo de
la variante TE-nativa (heredada del duplicado recién fusionado) también
está presente. Resultado real verificado en producción: 32 jugadores
recién fusionados correctamente (Naomi Osaka, Karolina Pliskova, Sofia
Kenin, Donna Vekic, etc.) fueron reordenados DE VUELTA a la forma
TE-nativa incorrecta por el mismo `--apply` que debía limpiar el resto
de la tabla.

1 excepción real encontrada (NO se revierte): `player_id=24621`
("Barbara Gatica"/"Gatica Barbara") -- en ESE caso el merge anterior
había dejado por error la forma TE-nativa como sobreviviente, y este
mismo `--apply` la corrigió a la forma correcta. Verificado a mano
contra el log real de producción antes de excluirlo.

Este script es un fix PUNTUAL de este incidente específico (mapa fijo
de 32 `player_id`->nombre correcto, no un heurístico general) --
NO reemplaza la necesidad de arreglar `find_reorder_candidates` para
que no dispare sobre jugadores recién fusionados (deuda técnica
documentada, ver memoria del proyecto).

Uso:
    python3 -m scripts.tennis.revert_reorder_regression_after_merge --dry-run
    python3 -m scripts.tennis.revert_reorder_regression_after_merge --apply
"""

from __future__ import annotations

import argparse

from database.historical.tennis import Player

# player_id -> canonical_name CORRECTO (el que tenía ANTES del --apply
# regresivo, verificado a mano contra el log real de producción del
# 2026-08-10). Gatica (24621) EXCLUIDO a propósito -- ver docstring.
_CORRECT_NAMES: dict[int, str] = {
    24588: "Naomi Osaka",
    24589: "Shuai Zhang",
    24590: "Zeynep Sonmez",
    24591: "Ayla Aksu",
    24593: "Oleksandra Oliynykova",
    24594: "Sinja Kraus",
    24595: "Anhelina Kalinina",
    24597: "Edward Winter",
    24598: "Whitney Osuigwe",
    24599: "Sofia Kenin",
    24600: "Anastasia Zakharova",
    24601: "Solana Sierra",
    24602: "Mananchaya Sawangkaew",
    24603: "Karolina Pliskova",
    24604: "Eva Lys",
    24605: "Donna Vekic",
    24607: "Fiona Ferro",
    24608: "Dalma Galfi",
    24609: "Carole Monnet",
    24620: "Arianne Hartono",
    24664: "Daniela Vismane",
    24747: "Hiroko Kuwata",
    24770: "Jana Fett",
    24791: "Jessika Ponchet",
    24792: "Karin Knapp",
    24793: "Katarina Zavatska",
    24794: "Madison Brengle",
    24795: "Margaux Rouvroy",
    24797: "Polona Hercog",
    24798: "Tamira Paszek",
    24799: "Ylena In-Albon",
    24805: "Daniel Merida Aguilar",
}


def run(apply: bool) -> dict:
    reverted = []
    skipped = []
    for player_id, correct_name in _CORRECT_NAMES.items():
        player = Player.get_or_none(Player.id == player_id)
        if player is None:
            skipped.append({"player_id": player_id, "reason": "not found"})
            continue
        if player.canonical_name == correct_name:
            skipped.append({"player_id": player_id, "reason": "already correct"})
            continue
        entry = {
            "player_id": player_id,
            "from": player.canonical_name,
            "to": correct_name,
        }
        reverted.append(entry)
        if apply:
            player.canonical_name = correct_name
            player.save()
    return {"mode": "apply" if apply else "dry-run", "reverted": reverted, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = run(apply=args.apply)
    print(f"[revert_reorder_regression_after_merge] mode={result['mode']}")
    for row in result["reverted"]:
        verb = "REVERTED" if args.apply else "WOULD REVERT"
        print(f"  {verb}: player_id={row['player_id']} '{row['from']}' -> '{row['to']}'")
    for row in result["skipped"]:
        print(f"  SKIPPED: player_id={row['player_id']} ({row['reason']})")


if __name__ == "__main__":
    main()
