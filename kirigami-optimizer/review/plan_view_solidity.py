import json, sys, numpy as np
from pathlib import Path
sys.path.insert(0, '/home/user/kiragami_workflow/kirigami-optimizer')
import config
config.SIMSCALE["GEOMETRY_FORMAT"] = "MANUAL_FLOW_VOLUME"; config.SIMSCALE["STEP_CHECK"] = False
import trimesh
from PIL import Image, ImageDraw
from pipeline import build_geometry
led = json.load(open('/home/user/kiragami_workflow/kirigami-optimizer/state/ledger.json'))['records']
out = {}
N = 1200
for r in led:
    if r['status'] != 'ok' or r['design_id'] == 'design_0043': continue
    p = dict(r['params']); p['EXTRUDE_METHOD'] = 'legacy'
    d = Path(sys.argv[1]) / ('b_' + r['design_id']); d.mkdir(exist_ok=True, parents=True)
    _, info, _, paths = build_geometry(p, d, r['design_id'])
    m = trimesh.load(paths['canopy_stl'], process=False)
    xy = m.triangles[:, :, :2]
    R = 5.2
    img = Image.new('L', (N, N), 0); dr = ImageDraw.Draw(img)
    s = N / (2 * R)
    for t in xy:
        dr.polygon([((x + R) * s, (y + R) * s) for x, y in t], fill=255)
    a = np.array(img) > 0
    yy, xx = np.mgrid[0:N, 0:N]; rr = np.hypot((xx - N / 2) / s, (yy - N / 2) / s)
    rp = info['projected_radius_in']
    disk = rr <= rp
    sol = a[disk].mean()
    out[r['design_id']] = dict(cd=r['cd'], solidity=float(sol), rings=p['NUM_RINGS'], cuts=p['CUTS_PER_RING'], depth=info['canopy_depth_in'],
                               ov=p.get('RING_OVERLAP_FRACTION'), ri=p['INNER_RADIUS'], ex=p['RING_SPACING_EXPONENT'], area=info['reference_area_in2'], surf=info['surface_area_in2'])
    print(r['design_id'], out[r['design_id']], flush=True)
json.dump(out, open(Path(sys.argv[1]) / 'solidity.json', 'w'), indent=1)
