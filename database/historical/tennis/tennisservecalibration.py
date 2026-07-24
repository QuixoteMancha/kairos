from peewee import CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisServeCalibration(HistoricalBaseModel):
    """One row per (tour, rank bucket, calibration run) snapshot of the
    real p_serve calibration — see
    outer_apis/tennis_serve_stats.py::calibrate_serve_pct_by_bucket, which feeds
    models/volatility/sports/tennis/tennis_markov.py. Persisting this
    over time lets the calibration be tracked/audited across runs (is
    p_serve drifting? is n_matches per bucket growing?) instead of only
    living in the 6h in-process cache."""

    class Meta:
        table_name = "tennis_serve_calibration"
        indexes = ((("tour", "bucket_min", "bucket_max"), False),)

    tour = CharField()  # "ATP" | "WTA"
    bucket_min = IntegerField()
    bucket_max = IntegerField()
    p_serve = FloatField()
    n_matches = IntegerField()
    n_players = IntegerField()
    calibrated_at = DateTimeField()
