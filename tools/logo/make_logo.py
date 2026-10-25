"""Draws the Mesh Mix-S2C logo as SVG: logo.svg (with the MM-S2C badge, used at
64 px and up) and logo-small.svg (no badge, for 16 and 32 px, where the text
would blur). render.js turns them into PNGs with Playwright
(node render.js logo logo-small), and the app icons are resized from those."""
import numpy as np, math
W=1024
BG='''<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1d26"/><stop offset="1" stop-color="#0d0f14"/></linearGradient>
<radialGradient id="teal" cx="0.38" cy="0.3" r="0.8"><stop offset="0" stop-color="#2bc4dc"/><stop offset="0.55" stop-color="#127a8c"/><stop offset="1" stop-color="#062a33"/></radialGradient>
<linearGradient id="meshshade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b9c2d0"/><stop offset="1" stop-color="#4a505c"/></linearGradient>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<clipPath id="tile"><rect x="100" y="100" width="824" height="824" rx="180"/></clipPath>
</defs>'''
def tile():
    s='<rect x="100" y="100" width="824" height="824" rx="180" fill="url(#bg)"/>'
    g=''.join(f'<line x1="{x}" y1="100" x2="{x}" y2="924" stroke="#ffffff" stroke-opacity="0.04" stroke-width="2"/>' for x in range(164,924,64))
    g+=''.join(f'<line x1="100" y1="{y}" x2="924" y2="{y}" stroke="#ffffff" stroke-opacity="0.04" stroke-width="2"/>' for y in range(164,924,64))
    return s+f'<g clip-path="url(#tile)">{g}</g>'
def icosphere(n=2):
    t=(1+5**.5)/2
    V=[(-1,t,0),(1,t,0),(-1,-t,0),(1,-t,0),(0,-1,t),(0,1,t),(0,-1,-t),(0,1,-t),(t,0,-1),(t,0,1),(-t,0,-1),(-t,0,1)]
    F=[(0,11,5),(0,5,1),(0,1,7),(0,7,10),(0,10,11),(1,5,9),(5,11,4),(11,10,2),(10,7,6),(7,1,8),(3,9,4),(3,4,2),(3,2,6),(3,6,8),(3,8,9),(4,9,5),(2,4,11),(6,2,10),(8,6,7),(9,8,1)]
    V=[np.array(v)/np.linalg.norm(v) for v in V]
    for _ in range(n):
        cache={};NF=[]
        def mid(a,b):
            k=tuple(sorted((a,b)))
            if k not in cache:
                m=V[a]+V[b];V.append(m/np.linalg.norm(m));cache[k]=len(V)-1
            return cache[k]
        for a,b,c in F:
            ab,bc,ca=mid(a,b),mid(b,c),mid(c,a);NF+=[(a,ab,ca),(b,bc,ab),(c,ca,bc),(ab,bc,ca)]
        F=NF
    return np.array(V),F
R=np.array([[1,0,0],[0,math.cos(.35),-math.sin(.35)],[0,math.sin(.35),math.cos(.35)]])@np.array([[math.cos(.3),0,math.sin(.3)],[0,1,0],[-math.sin(.3),0,math.cos(.3)]])
def mesh_half(cx,cy,r,side='left',n=2):
    V,F=icosphere(n);V=V@R.T;L=np.array([-.5,-.6,.62]);L/=np.linalg.norm(L)
    out=[]
    for f in F:
        P=V[list(f)];nrm=P.mean(0)
        if nrm[2]<=0: continue
        xs=P[:,0]
        if side=='left' and xs.max()>0.02: 
            if xs.min()>-0.0: continue
        sh=max(0,np.dot(nrm/np.linalg.norm(nrm),L));c=int(70+150*sh)
        pts=' '.join(f'{cx+r*p[0]:.1f},{cy+r*p[1]:.1f}' for p in P)
        out.append((nrm[2],f'<polygon points="{pts}" fill="rgb({c},{c+6},{c+16})" stroke="#0d0f14" stroke-width="5" stroke-linejoin="round"/>'))
    out.sort()
    return ''.join(s for _,s in out)
def svg(body): return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}">{BG}{tile()}{body}</svg>'
cx,cy,r=512,512,300
clipL=f'<clipPath id="L"><rect x="0" y="0" width="{cx}" height="{W}"/></clipPath>'
clipR=f'<clipPath id="Rr"><rect x="{cx}" y="0" width="{W}" height="{W}"/></clipPath>'
def curves(cx,cy,r,color="#9ff0ff",op=0.55):
    s=''
    for lat in [-60,-30,0,30,60]:
        y=cy+r*math.sin(math.radians(lat))*math.cos(.35); rx=r*math.cos(math.radians(lat)); ry=rx*math.sin(.35)
        s+=f'<ellipse cx="{cx}" cy="{y:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="none" stroke="{color}" stroke-opacity="{op}" stroke-width="5"/>'
    for lon in [20,50,75]:
        rx=r*math.sin(math.radians(lon))
        s+=f'<ellipse cx="{cx}" cy="{cy}" rx="{rx:.1f}" ry="{r:.1f}" fill="none" stroke="{color}" stroke-opacity="{op}" stroke-width="5"/>'
    return s
# A: mesh | smooth sphere with CAD curves (isolines) + divider
A=f'<defs>{clipL}{clipR}</defs><g clip-path="url(#L)">{mesh_half(cx,cy,r)}</g><g clip-path="url(#Rr)"><circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#teal)"/>{curves(cx,cy,r)}</g><line x1="{cx}" y1="{cy-r-40}" x2="{cx}" y2="{cy+r+40}" stroke="#2ad4f0" stroke-opacity="0.35" stroke-width="30" stroke-linecap="round"/><line x1="{cx}" y1="{cy-r-40}" x2="{cx}" y2="{cy+r+40}" stroke="#2ad4f0" stroke-width="12" stroke-linecap="round"/>'
open('logo-small.svg','w').write(svg(A))
# B: A plus S2C badge
badge='<rect x="327" y="782" width="370" height="118" rx="46" fill="#0d0f14" fill-opacity="0.6"/><rect x="335" y="774" width="354" height="110" rx="42" fill="#2ad4f0"/><text x="512" y="851" font-family="DejaVu Sans, Arial, sans-serif" font-weight="bold" font-size="64" text-anchor="middle" fill="#0d0f14">MM-S2C</text>'
open('logo.svg','w').write(svg(A+badge))
