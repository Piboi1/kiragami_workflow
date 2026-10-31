import sys, re, numpy as np
def blocks(path):
    cur=None; out=[]
    for line in open(path):
        s=line.strip()
        if s.startswith('solid '): cur=[]
        elif s.startswith('vertex'): cur.append([float(v) for v in s.split()[1:4]])
        elif s.startswith('endsolid'): out.append(np.array(cur).reshape(-1,3,3)); cur=None
    return out
for tag in ('legacy','lattice'):
    bl=blocks(f'{sys.argv[1]}/build_{tag}/design_0023_canopy_presplit.stl')
    tri=np.concatenate(bl)
    e=np.concatenate([np.linalg.norm(tri[:,1]-tri[:,0],axis=1),np.linalg.norm(tri[:,2]-tri[:,1],axis=1),np.linalg.norm(tri[:,0]-tri[:,2],axis=1)])
    nt=np.array([len(b) for b in bl])
    area=0.5*np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)
    print(f'== {tag}: {len(bl)} blocks, {len(tri)} tris, tri/block median {np.median(nt):.0f} min {nt.min()} max {nt.max()}')
    print('   edge length in: pct 0/1/5/25/50/75/95/100 =', np.round(np.percentile(e,[0,1,5,25,50,75,95,100]),4))
    print('   share of edges < 0.005in: %.1f%%, <0.01: %.1f%%, <0.02: %.1f%%'%tuple(100*(e<t).mean() for t in (0.005,0.01,0.02)))
    print('   total tri area %.1f in2'%area.sum())
    # per block bbox min dimension (thickness proxy)
    mins=[]; 
    for b in bl:
        v=b.reshape(-1,3); c=v-v.mean(0); sv=np.linalg.svd(c,compute_uv=False)
        mins.append(sv)
    mins=np.array(mins)/np.sqrt(np.array([len(b)*3 for b in bl]))[:,None]
    print('   piece extents (rms along PCA axes) median:', np.round(np.median(mins,axis=0),4))
