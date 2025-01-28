from typing import List, NamedTuple, Union
from abc import ABCMeta, abstractmethod
import torch
from gaussian_splatting import GaussianModel
from .abc import Motion, MotionEstimator

from instantsplatstream.dataset import DatasetCameraMeta, VideoCameraDataset


class FixedViewFrameSequenceMeta(NamedTuple):
    image_height: int
    image_width: int
    FoVx: float
    FoVy: float
    R: torch.Tensor  # TODO: quaternion maybe better?
    T: torch.Tensor
    frames_path: List[str]

    @classmethod
    def from_datasetcameras(cls, cameras: List[DatasetCameraMeta]) -> 'FixedViewFrameSequenceMeta':
        for camera in cameras:
            assert camera.image_height == cameras[0].image_height
            assert camera.image_width == cameras[0].image_width
            assert abs(camera.FoVx - cameras[0].FoVx) < 1e-8
            assert abs(camera.FoVy - cameras[0].FoVy) < 1e-8
            assert torch.isclose(camera.R, cameras[0].R).all()
            assert torch.isclose(camera.T, cameras[0].T).all()
        return cls(
            image_height=camera.image_height,
            image_width=camera.image_width,
            FoVx=camera.FoVx,
            FoVy=camera.FoVy,
            R=camera.R,
            T=camera.T,
            frames_path=[camera.image_path for camera in cameras]
        )


class FixedViewBatchMotionEstimator(metaclass=ABCMeta):
    @abstractmethod
    def to(self, device: torch.device) -> 'MotionEstimator':
        return self

    @abstractmethod
    def __call__(self, views: List[FixedViewFrameSequenceMeta]) -> List[Motion]:
        raise NotImplementedError

    @abstractmethod
    def update_baseframe(self, frame: GaussianModel) -> 'FixedViewBatchMotionEstimator':
        return self


class FixedViewMotionEstimator(MotionEstimator):
    def __init__(self, dataset: VideoCameraDataset, batch_func: FixedViewBatchMotionEstimator, batch_size=2, device=torch.device("cuda")):
        super().__init__()
        cameras = dataset.get_metas()
        for frame in cameras:
            assert len(frame) == len(cameras[0])
        self.cameras = [FixedViewFrameSequenceMeta.from_datasetcameras(frame) for frame in zip(*cameras)]
        self.batch_func = batch_func
        self.batch_size = batch_size
        self.to(device)

    def to(self, device: torch.device) -> 'MotionEstimator':
        self.batch_func = self.batch_func.to(device)
        return self

    @property
    def frames(self) -> List[FixedViewFrameSequenceMeta]:
        '''So you can access the views like this: estimator.frames[0] or estimator.frames[0:10]'''
        class ViewCollector:
            def __init__(self, cameras: List[FixedViewFrameSequenceMeta]):
                self.cameras = cameras
                for camera in self.cameras:
                    assert len(camera.frames_path) == len(self.cameras[0].frames_path)

            def __getitem__(self, frame_idx: Union[int, slice]) -> List[FixedViewFrameSequenceMeta]:
                if isinstance(frame_idx, slice):
                    return [camera._replace(frames_path=camera.frames_path[frame_idx]) for camera in self.cameras]
                if isinstance(frame_idx, int):
                    return [camera._replace(frames_path=[camera.frames_path[frame_idx]]) for camera in self.cameras]
                raise ValueError("frame_idx must be either an integer or a slice")
        return ViewCollector(self.cameras)

    def __iter__(self) -> 'FixedViewMotionEstimator':
        self.frame_idx = 0
        self.curr_initframe_idx = -1
        self.curr_motions = []
        return self

    def __next__(self) -> Motion:
        length = len(self.cameras[0].frames_path)
        for camera in self.cameras:
            assert len(camera.frames_path) == length
        self.frame_idx += 1
        if self.frame_idx >= length:
            raise StopIteration
        prevframe_idx = self.frame_idx - 1
        initframe_idx = (prevframe_idx // (self.batch_size-1)) * (self.batch_size-1)
        if initframe_idx + self.batch_size > length:
            initframe_idx = length - self.batch_size
        if initframe_idx != self.curr_initframe_idx:
            motions = self.batch_func(self.frames[initframe_idx:initframe_idx + self.batch_size])
            assert len(motions) == self.batch_size-1
            motions[-1] = motions[-1]._replace(update_baseframe=True)
            self.curr_motions = motions
            self.curr_initframe_idx = initframe_idx
        return self.curr_motions[self.frame_idx-initframe_idx-1]

    def update_baseframe(self, frame: GaussianModel) -> 'FixedViewMotionEstimator':
        self.batch_func = self.batch_func.update_baseframe(frame)
        return self
