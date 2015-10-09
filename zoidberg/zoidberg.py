from __future__ import division
from builtins import object
from past.utils import old_div

import numpy as np
from boututils import datafile as bdata

# PyEVTK might be called pyevtk or evtk, depending on where it was
# installed from
have_evtk = True
try:
    from pyevtk.hl import gridToVTK
except ImportError:
    try:
        from evtk.hl import gridToVTK
    except ImportError:
        have_evtk = False

import matplotlib.pyplot as plt

import grid
import field
import fieldtracer
from .progress import update_progress

def make_maps(grid, magnetic_field, quiet=False, **kwargs):
    """Make the forward and backward FCI maps

    Inputs
    ------
    grid           - Grid generated by Zoidberg
    magnetic_field - Zoidberg magnetic field object
    quiet          - Don't display progress bar [False]
    **kwargs       - Arguments for field line tracing, etc.
    """

    nx, ny, nz = (grid.nx, grid.ny, grid.nz)

    # Arrays to store X index at end of field-line
    # starting from (x,y,z) and going forward in toroidal angle (y)
    forward_xt_prime = np.zeros( (nx, ny, nz) )
    forward_zt_prime = np.zeros( (nx, ny, nz) )

    # Same but going backwards in toroidal angle
    backward_xt_prime = np.zeros( (nx, ny, nz) )
    backward_zt_prime = np.zeros( (nx, ny, nz) )

    x2d, z2d = np.meshgrid(grid.xarray, grid.zarray, indexing='ij')
    field_tracer = fieldtracer.FieldTracer(magnetic_field)

    # TODO: if axisymmetric, don't loop, do one slice and copy
    for j in range(ny):
        if not quiet:
            update_progress(float(j)/float(ny-1), **kwargs)

        x_coords = x2d.flatten()
        z_coords = z2d.flatten()

        # Go forwards from yarray[j] by an angle delta_y
        coord = field_tracer.follow_field_lines(x_coords, z_coords, [grid.yarray[j], grid.delta_y])[1,...]
        coord = coord.reshape( (grid.nx, grid.nz, 2) )
        forward_xt_prime[:,j,:] = coord[:,:,0] / grid.delta_x # X index
        forward_zt_prime[:,j,:] = coord[:,:,1] / grid.delta_z # Z index

        # Go backwards from yarray[j] by an angle -delta_y
        coord = field_tracer.follow_field_lines(x_coords, z_coords, [grid.yarray[j], -grid.delta_y])[1,...]
        coord = coord.reshape( (grid.nx, grid.nz, 2) )
        backward_xt_prime[:,j,:] = coord[:,:,0] / grid.delta_x # X index
        backward_zt_prime[:,j,:] = coord[:,:,1] / grid.delta_z # Z index

    maps = {
        'forward_xt_prime' : forward_xt_prime,
        'forward_zt_prime' : forward_zt_prime,
        'backward_xt_prime' : backward_xt_prime,
        'backward_zt_prime' : backward_zt_prime
    }

    return maps

def write_maps(grid, magnetic_field, maps, gridfile='fci.grid.nc', legacy=False):
    """Write FCI maps to BOUT++ grid file

    Inputs
    ------
    grid           - Grid generated by Zoidberg
    magnetic_field - Zoidberg magnetic field object
    maps           - Dictionary of FCI maps
    gridfile       - Output filename
    legacy         - If true, write FCI maps using FFTs
    """

    nx, ny, nz = (grid.nx, grid.ny, grid.nz)
    xarray, yarray, zarray = (grid.xarray, grid.yarray, grid.zarray)

    g_22 = np.zeros((nx,ny)) + 1./grid.Rmaj**2

    totalbx = np.zeros((nx,ny,nz))
    totalbz = np.zeros((nx,ny,nz))
    Bxy = np.zeros((nx,ny,nz))
    for i in np.arange(0,nx):
        for j in np.arange(0,ny):
            for k in np.arange(0,nz):
                Bxy[i,j,k] = np.sqrt((magnetic_field.Bxfunc(xarray[i],zarray[k],yarray[j])**2
                                      + magnetic_field.Bzfunc(xarray[i],zarray[k],yarray[j])**2))
                totalbx[i,j,k] = magnetic_field.Bxfunc(xarray[i],zarray[k],yarray[j])
                totalbz[i,j,k] = magnetic_field.Bzfunc(xarray[i],zarray[k],yarray[j])

    with bdata.DataFile(gridfile, write=True, create=True) as f:
        ixseps = nx+1
        f.write('nx', grid.nx)
        f.write('ny', grid.ny)
        if not legacy:
            # Legacy files don't need nz
            f.write('nz', grid.nz)

        f.write("dx", grid.delta_x)
        f.write("dy", grid.delta_y)

        f.write("ixseps1",ixseps)
        f.write("ixseps2",ixseps)

        f.write("g_22", g_22)

        f.write("Bxy", Bxy[:,:,0])
        f.write("bx", totalbx)
        f.write("bz", totalbz)

        # Legacy grid files need to FFT 3D arrays
        if legacy:
            from boutdata.input import transform3D
            f.write('forward_xt_prime',  transform3D(maps['forward_xt_prime']))
            f.write('forward_zt_prime',  transform3D(maps['forward_zt_prime']))

            f.write('backward_xt_prime', transform3D(maps['backward_xt_prime']))
            f.write('backward_zt_prime', transform3D(maps['backward_zt_prime']))
        else:
            f.write('forward_xt_prime',  maps['forward_xt_prime'])
            f.write('forward_zt_prime',  maps['forward_zt_prime'])

            f.write('backward_xt_prime', maps['backward_xt_prime'])
            f.write('backward_zt_prime', maps['backward_zt_prime'])


def fci_to_vtk(infile, outfile, scale=5):

    if not have_evtk:
        return

    with bdata.DataFile(infile, write=False, create=False) as f:
        dx = f.read('dx')
        dy = f.read('dy')

        bx = f.read('bx')
        by = np.ones(bx.shape)
        bz = f.read('bz')
        if bx is None:
            xt_prime = f.read('forward_xt_prime')
            zt_prime = f.read('forward_zt_prime')
            array_indices = indices(xt_prime.shape)
            bx = xt_prime - array_indices[0,...]
            by = by * dy
            bz = zt_prime - array_indices[2,...]

        nx, ny, nz = bx.shape
        dz = nx*dx / nz

    x = np.linspace(0, nx*dx, nx)
    y = np.linspace(0, ny*dy, ny)
    z = np.linspace(0, nz*dz, nz)

    gridToVTK(outfile, x*scale, y, z*scale, pointData={'B' : (bx*scale, by, bz*scale)})
