from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import pytest
from AeroSlice.model import box_mesh,write_stl

@pytest.fixture
def cube(tmp_path):
    path=tmp_path/'cube.stl';write_stl(path,box_mesh(np.array([-15.,-15.,0.]),np.array([15.,15.,30.])))
    return path
