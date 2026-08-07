import math
from pathlib import Path


PI_SCALE = 100


class CircleService:
    def __init__(self, radius: float) -> None:
        self.radius = radius

    def area(self) -> float:
        return math.pi * self.radius * self.radius

    def diameter(self) -> float:
        return self.radius * 2


def build_cache_path(name: str) -> Path:
    base = Path('/tmp')
    return base / f'{name}.json'
