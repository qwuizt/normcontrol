from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

import numpy as np

from src.structures import PageElementDetail


class AbstractDetectionModel(ABC):
    def __init__(self, path_to_weights: str, filename_weights: str, conf: float = 0.2, iou: float = 0.8) -> None:
        self.path_to_weights = Path(path_to_weights) / filename_weights
        if not self.path_to_weights.exists():
            raise ValueError(f'Path to weights: "{self.path_to_weights}" not exists.')

        self.conf: float = conf
        self.iou: float = iou

    @abstractmethod
    def get_model(self):
        raise NotImplementedError()

    @abstractmethod
    def detect_everything(self, img: np.ndarray, page_index: int) -> Iterator[PageElementDetail]:
        raise NotImplementedError()
