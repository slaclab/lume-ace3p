from netCDF4 import Dataset
import numpy as np
import pyvista as pv
import random

#load ncdf file and extract the exterior tetrahedra and coordinates
def read_ncdf(filename):
    ds = Dataset(filename)
    t_ext = ds.variables['tetrahedron_exterior'][:]
    coords  = ds.variables['coords'][:]

    #extract the tetrahedra and flags(boundary conditions)
    tets = t_ext[:,1:5]
    Tflags = t_ext[:,5:9]

    #extract the faces of the tetrahedra
    face0 = tets[:, [0, 2, 1]]
    face1 = tets[:, [0, 3, 2]]
    face2 = tets[:, [0, 1, 3]]
    face3 = tets[:, [1, 2, 3]]

    #combine all faces in array and group them by boundary condition flag
    all_tris = np.stack([face0, face1, face2, face3], axis=1).reshape(-1, 3)
    tflags_reshaped = Tflags.reshape(-1)

    sidesets = np.unique(tflags_reshaped)
    sidesets = sidesets[sidesets > 0]

    tris_by_sideset = {}
    for side in sidesets:
        tris_by_sideset[int(side)] = all_tris[tflags_reshaped == side]

    return np.asarray(coords), tris_by_sideset

#stitch the copies together by reflecting coords and updating the triangle indices
def stitch_copies(coords, tris_by_sideset, patterns):
    V = coords.shape[0]
    new_coords = np.vstack([coords * np.array(signs) for signs in patterns])

    new_tris_by_sideset = {}
    for sideset, tris in tris_by_sideset.items():
        copies = [tris + i * V for i in range(len(patterns))]
        new_tris_by_sideset[sideset] = np.vstack(copies)

    return new_coords, new_tris_by_sideset

def apply_symmetry(coords, tris, sym_type):
    if sym_type == "full":
        return coords, tris
    elif sym_type == "halfx":          # flip Y (reflect across xz-plane)
        patterns = [(1, 1, 1), (1, -1, 1)]
    elif sym_type == "halfy":          # flip X (reflect across yz-plane)
        patterns = [(1, 1, 1), (-1, 1, 1)]
    elif sym_type == "quarter":        # flip both X and Y
        patterns = [(1, 1, 1), (1, -1, 1), (-1, -1, 1), (-1, 1, 1)]
    else:
        raise ValueError("Invalid symmetry type")
    return stitch_copies(coords, tris, patterns)

#load the mesh and apply symmetry
def load_mesh(filename, sym_type):
    coords, tris = read_ncdf(filename)
    coords, tris = apply_symmetry(coords, tris, sym_type)
    return coords, tris

SIDESET_COLORS = ['red', 'darkred', 'crimson', 'salmon', 'tomato', 'orange',
                  'gold', 'khaki', 'yellow', 'green', 'lightgreen', 'seagreen',
                  'teal', 'cyan', 'lightblue', 'cornflowerblue', 'royalblue',
                  'navy', 'purple', 'plum', 'magenta', 'pink', 'hotpink',
                  'brown', 'tan', 'beige', 'gray', 'silver']

#render each sideset in its own color with a legend
def render(coords, tris_by_sideset, cav_radius, ellipticity):
    rw = pv.Plotter()
    rw.set_background("beige")

    colors = list(SIDESET_COLORS)
    random.shuffle(colors)

    for i, (sideset, tris) in enumerate(sorted(tris_by_sideset.items())):
        num = tris.shape[0]
        faces = np.hstack([np.full((num, 1), 3), tris]).ravel()
        mesh = pv.PolyData(coords, faces)
        rw.add_mesh(mesh, color=colors[i % len(colors)],
                    show_edges=True, label=f"sideset {sideset}")

    rw.add_legend(size=(0.15, 0.15), loc='lower right')
    rw.show_grid(
        xtitle='X', ytitle='Y', ztitle='Z',
        font_size=20, font_family='times'
    )
    # rw.add_text(f"cav_radius = {cav_radius}, ellipticity = {ellipticity}",
    #             position="upper_edge", color="black", font_size=12)
    rw.camera_position = 'zx'
    rw.show_axes()
    rw.show()

def main():
    filename = "pillbox-rtop4.ncdf"
    sym_type = 'full'  # quarter, halfx, halfy, full
    cav_radius = 90
    ellipticity = 0.5

    coords, tris = load_mesh(filename, sym_type)
    print("Number of vertices:", coords.shape,
          "| sidesets:", {k: v.shape[0] for k, v in tris.items()})
    render(coords, tris, cav_radius, ellipticity)

if __name__ == "__main__":
    main()
