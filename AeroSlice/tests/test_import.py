from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import struct
import numpy as np
import pytest
from AeroSlice.file_import import load_model,CORE
from AeroSlice.model import write_stl,box_mesh


def test_binary_and_preservation(cube):
    original=cube.read_bytes();m=load_model(cube)
    assert m.kind=='STL geometry';assert np.allclose(m.dimensions,[30,30,30]);assert cube.read_bytes()==original
    m.verify_original();cube.write_bytes(original+b'x')
    with pytest.raises(ValueError,match='changed'):m.verify_original()


def test_ascii(tmp_path):
    triangles=box_mesh(np.zeros(3),np.array([2.,3.,4.]))
    text='solid box\n'
    for face in triangles:
        text+='facet normal 0 0 0\nouter loop\n'+''.join(f'vertex {x} {y} {z}\n' for x,y,z in face)+'endloop\nendfacet\n'
    text+='endsolid box\n';p=tmp_path/'ascii.stl';p.write_text(text)
    assert np.allclose(load_model(p).dimensions,[2,3,4])


def geometry_3mf(path,triangles,items=1,transform='1 0 0 0 1 0 0 0 1 10 20 30'):
    vs=''.join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x,y,z in triangles.reshape(-1,3))
    fs=''.join(f'<triangle v1="{3*i}" v2="{3*i+1}" v3="{3*i+2}"/>' for i in range(len(triangles)))
    xml=f'<model xmlns="{CORE}" unit="millimeter"><resources><object id="1"><mesh><vertices>{vs}</vertices><triangles>{fs}</triangles></mesh></object></resources><build>'+''.join(f'<item objectid="1" transform="{transform}"/>' for _ in range(items))+'</build></model>'
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        z.writestr('3D/3dmodel.model',xml)
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rel-1" Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def test_geometry_3mf_transforms(tmp_path):
    p=tmp_path/'cube.3mf';geometry_3mf(p,box_mesh(np.zeros(3),np.array([2.,3.,4.])))
    m=load_model(p);assert m.kind=='Geometry 3MF';assert np.allclose(m.bounds,[[10,20,30],[12,23,34]])


def test_multiobject_rejected(tmp_path):
    p=tmp_path/'multi.3mf';geometry_3mf(p,box_mesh(np.zeros(3),np.ones(3)),2)
    with pytest.raises(ValueError,match='one build'):load_model(p)

@pytest.mark.parametrize('content',[b'',b'not an stl',b'solid bad\nendsolid bad',b'\0'*83,b'PK\x03\x04broken'])
def test_invalid(tmp_path,content):
    p=tmp_path/'bad.stl';p.write_bytes(content)
    with pytest.raises(ValueError):load_model(p)


def test_missing_file(tmp_path):
    with pytest.raises(ValueError,match='does not exist'):load_model(tmp_path/'missing.stl')


def test_open_mesh(tmp_path):
    p=tmp_path/'open.stl';write_stl(p,box_mesh(np.zeros(3),np.ones(3))[:-1])
    with pytest.raises(ValueError,match='nonmanifold'):load_model(p)


def test_replacement(cube,tmp_path):
    first=load_model(cube);other=tmp_path/'second.stl';write_stl(other,box_mesh(np.zeros(3),np.array([5.,6.,7.])))
    second=load_model(other);assert first.path!=second.path;assert np.allclose(second.dimensions,[5,6,7]);first.verify_original()


def test_orientation_y_is_right_handed(cube):
    model=load_model(cube);actual=model.oriented((0.,90.,0.))
    expected=model.triangles[:,:,[2,1,0]].copy();expected[:,:,2]*=-1
    expected-= (expected.min(axis=(0,1))+expected.max(axis=(0,1)))/2
    expected[:,:,2]-=expected[:,:,2].min()
    assert np.allclose(actual,expected,atol=1e-10)
