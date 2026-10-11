"""Geometry primitives. Coordinates are millimeters; STL units are assumed mm."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import struct
import numpy as np

@dataclass
class Model:
    path: Path
    kind: str
    triangles: np.ndarray
    sha256: str
    warnings: list[str]

    @property
    def bounds(self) -> np.ndarray:
        return np.array([self.triangles.min(axis=(0, 1)), self.triangles.max(axis=(0, 1))])

    @property
    def dimensions(self) -> np.ndarray:
        return np.diff(self.bounds, axis=0)[0]

    def oriented(self, rotation: tuple[float, float, float]) -> np.ndarray:
        points = self.triangles.copy()
        for axis, degrees in enumerate(rotation):
            angle = np.radians(degrees)
            c, s = np.cos(angle), np.sin(angle)
            a, b = ((1, 2), (2, 0), (0, 1))[axis]
            matrix = np.eye(3)
            matrix[a, a] = matrix[b, b] = c
            matrix[a, b], matrix[b, a] = -s, s
            points = points @ matrix.T
        bounds = np.array([points.min(axis=(0, 1)), points.max(axis=(0, 1))])
        points -= (bounds[0] + bounds[1]) / 2
        points[:, :, 2] -= points[:, :, 2].min()
        return points

    def verify_original(self) -> None:
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.sha256:
            raise ValueError('The uploaded model changed on disk. Upload it again.')


def box_mesh(low: np.ndarray, high: np.ndarray) -> np.ndarray:
    x, y, z = low
    X, Y, Z = high
    vertices = np.array([(x,y,z),(X,y,z),(X,Y,z),(x,Y,z),(x,y,Z),(X,y,Z),(X,Y,Z),(x,Y,Z)])
    faces = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
             (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return vertices[np.array(faces)]


def write_stl(path: Path, triangles: np.ndarray) -> None:
    with path.open('wb') as stream:
        stream.write(b'AeroSlice geometry, millimeters'.ljust(80, b'\0'))
        stream.write(struct.pack('<I', len(triangles)))
        for triangle in triangles:
            normal = np.cross(triangle[1]-triangle[0], triangle[2]-triangle[0])
            norm = np.linalg.norm(normal)
            if norm: normal /= norm
            stream.write(struct.pack('<12fH', *normal, *triangle.ravel(), 0))


def geometry_digest(triangles: np.ndarray) -> str:
    """Translation invariant fingerprint, tolerant to Orca's float mesh storage."""
    pts = triangles - triangles.min(axis=(0, 1))
    faces = [tuple(sorted(tuple(np.round(v, 4)) for v in face)) for face in pts]
    return hashlib.sha256(repr(sorted(faces)).encode()).hexdigest()
