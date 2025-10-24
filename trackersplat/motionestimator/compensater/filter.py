import copy
import torch
import torch.nn.functional as F
from gaussian_splatting import GaussianModel
from gaussian_splatting.utils import quaternion_to_matrix
from trackersplat.motionestimator import Motion
from trackersplat.utils.simple_knn import knn_kernel
from trackersplat.utils import motion_median_filter

from .base import BaseMotionCompensater


class FilteredMotionCompensater(BaseMotionCompensater):
    def __init__(self, k: int = 8, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.k = k

    def update_knn(self, gaussians: GaussianModel, k: int) -> 'FilteredMotionCompensater':
        xyz = gaussians.get_xyz.detach()
        assert k <= xyz.size(0), "k should be less than the number of points"

        # k nearest neighbors of each points
        self.neighbor_indices, dists = knn_kernel(xyz, k)
        self.neighbor_weights = torch.exp(-F.normalize(dists))
        self.neighbor_relative_dists_last = dists

        # vector from each points to their k nearest neighbors (a.k.a. "neighbor offsets")
        self.neighbor_offsets_last = xyz[self.neighbor_indices] - xyz.unsqueeze(-2)

        # rotation matrix of each points
        self.rotation_matrix_last = quaternion_to_matrix(gaussians.get_rotation.detach())
        self.rotation_matrix_inv_last = self.rotation_matrix_last.transpose(2, 1)

        # "neighbor offsets" in the local coordinate system of each points
        self.neighbor_offsets_point_coord_last = (
            self.rotation_matrix_inv_last.unsqueeze(1) @ self.neighbor_offsets_last.unsqueeze(-1)
        ).squeeze(-1)
        return self

    def update_baseframe(self, frame) -> 'FilteredMotionCompensater':
        return super().update_baseframe(frame).update_knn(frame, self.k)

    def median_filter_neighbor_transformation(self, translation_vector: torch.Tensor, motion_mask_mean: torch.Tensor) -> torch.Tensor:
        assert motion_mask_mean is not None, "Translation mask is required"
        median_translation_vector = motion_median_filter(
            mask=motion_mask_mean.clone(),
            motion=translation_vector,
            neighbor_indices=self.neighbor_indices, neighbor_weights=self.neighbor_weights
        )
        return median_translation_vector

    def compensate(self, baseframe: GaussianModel, motion: Motion) -> GaussianModel:
        '''Overload this method to make your own compensation'''
        currframe = copy.deepcopy(baseframe)
        median_translation_vector = self.median_filter_neighbor_transformation(motion.translation_vector, motion.motion_mask_mean)
        motion = motion._replace(translation_vector=median_translation_vector)
        return super().compensate(currframe, motion)
