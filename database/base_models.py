from peewee import Model

from database.db import db, db_historical


class BaseModel(Model):
    class Meta:
        database = db


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class ArchiveBaseModel(Model):
    class Meta:
        database = db_historical
