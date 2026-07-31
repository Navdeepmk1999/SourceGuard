import json

from pgvector.sqlalchemy import Vector
from sqlalchemy.types import Text, TypeDecorator


class PortableVector(TypeDecorator):
    """pgvector `Vector(dimensions)` on PostgreSQL; JSON-encoded float list on
    other dialects (e.g. SQLite) so ORM tests can run without a real Postgres instance."""

    impl = Text
    cache_ok = True
    # Exposes .cosine_distance() / .l2_distance() / .max_inner_product() on the
    # mapped column for query building, regardless of the underlying dialect.
    comparator_factory = Vector.Comparator

    def __init__(self, dimensions: int, *args, **kwargs) -> None:
        self.dimensions = dimensions
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dimensions))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None or dialect.name == "postgresql":
            return value
        return json.loads(value)
