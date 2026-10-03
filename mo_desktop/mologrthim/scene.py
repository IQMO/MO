"""Antialiased, skin-derived scenery for the native Mologrthim canvas."""
from __future__ import annotations


def mix(a, b, amount):
    from PIL import ImageColor
    ca = ImageColor.getrgb(a) if isinstance(a, str) else a
    cb = ImageColor.getrgb(b) if isinstance(b, str) else b
    return tuple(round(x + (y - x) * amount) for x, y in zip(ca, cb))


def room(size, palette, *, foreground=False):
    """Cached room plates with one coherent projected grid and soft local light."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    scale = 2
    image = Image.new('RGBA', (round(w*scale), round(h*scale)))
    sx, sy = image.width/1600, image.height/1000
    draw = ImageDraw.Draw(image)
    def poly(points, color):
        draw.polygon([(round(x*sx),round(y*sy)) for x,y in points],fill=color)
    def line(points, color, width=1):
        draw.line([(round(x*sx),round(y*sy)) for x,y in points],fill=color,width=max(1,round(width*sx)))
    floor=mix(palette.entry,palette.muted,.34)
    wall=mix(palette.entry,palette.border,.50)
    edge=mix(palette.border,palette.muted,.36)
    def box(x,y,bw,depth,height):
        top=[(x,y),(x+depth*.70,y-depth*.54),(x+bw+depth*.70,y-depth*.54+bw*.18),(x+bw,y+bw*.18)]
        poly([(x,y),(x+bw,y+bw*.18),(x+bw,y+bw*.18+height),(x,y+height)],mix(palette.entry,palette.border,.40))
        poly([top[2],top[3],(x+bw,y+bw*.18+height),(top[2][0],top[2][1]+height)],palette.entry)
        poly(top,edge)
        line(top+[top[0]],mix(edge,palette.text,.16))
        line([(x+2,y+3),(x+bw-2,y+bw*.18+3)],mix(edge,palette.text,.07))
        line([(x,y+height),(x+bw,y+bw*.18+height)],mix(palette.entry,palette.border,.65))
    if not foreground:
        draw.rectangle((0,0,image.width,image.height),fill=palette.card)
        poly([(32,475),(315,282),(1128,431),(758,833),(159,701)],floor)
        poly([(32,475),(159,701),(758,833),(1128,431),(1128,465),(762,865),(150,730),(32,510)],mix(palette.entry,palette.border,.4))
        for i in range(1,12):
            t=i/12
            line([(315+813*t,282+149*t),(32+726*t,475+358*t)],mix(floor,palette.text,.075))
        for i in range(1,9):
            t=i/9
            line([(32+283*t,475-193*t),(758+370*t,833-402*t)],mix(floor,palette.text,.075))
        poly([(70,169),(315,60),(1128,175),(1128,431),(315,282),(70,475)],wall)
        poly([(70,169),(315,60),(1128,175),(1114,186),(316,73),(87,179)],edge)
        poly([(70,169),(87,179),(87,462),(70,475)],edge)
        line([(315,73),(315,282)],palette.border,2)
        line([(1114,186),(1114,434)],edge,12)
        # Dark skirting anchors the walls to the projected floor.
        line([(88,450),(316,283),(1113,430)],mix(palette.entry,palette.border,.25),13)
        line([(88,441),(316,274),(1113,421)],mix(wall,palette.text,.10),2)
        # Recessed wall taskboard and warm overhead bar.
        poly([(366,109),(808,173),(808,354),(366,289)],palette.entry)
        line([(366,109),(808,173),(808,354),(366,289),(366,109)],edge,8)
        line([(393,129),(774,185)],mix(palette.warn,palette.text,.55),3)
        line([(378,119),(796,180),(796,339)],mix(palette.border,palette.entry,.35),5)
        for x in (520,655):
            line([(x,141+(x-393)*.14),(x,305+(x-393)*.14)],mix(wall,palette.border,.25))
        # Archive's three shelves; neutral spines are furniture, never fake records.
        for y in (250,302,354):
            box(853,y,210,24,12)
            for j in range(9):
                box(868+j*21,y-37+j*3.8,16,22,36)
                line([(872+j*21,y-13+j*3.8),(880+j*21,y-11+j*3.8)],mix(edge,palette.muted,.35),2)
        # Learning alcove and front partitions.
        poly([(830,462),(1128,514),(1128,680),(830,628)],wall)
        line([(830,462),(1128,514),(1128,680)],edge,12)
        poly([(83,484),(182,431),(182,580),(83,634)],wall)
        poly([(735,588),(822,535),(822,720),(735,770)],wall)
        # Low front edge, recessed doorway and a short threshold.
        poly([(158,701),(673,804),(673,836),(151,731)],mix(wall,palette.border,.16))
        poly([(678,711),(785,643),(785,773),(678,842)],palette.entry)
        line([(678,842),(678,711),(785,643),(785,773)],edge,12)
        poly([(696,722),(766,678),(766,773),(696,817)],mix(palette.entry,palette.accent,.10))
        line([(704,718),(704,811),(761,775)],mix(palette.accent,palette.muted,.60),2)
        box(676,844,32,116,14)
        # A woven mat gives the learning corner a different material.
        mat=[(850,672),(1044,707),(975,775),(791,736)]
        poly(mat,mix(wall,palette.warn,.19))
        line(mat+[mat[0]],mix(edge,palette.warn,.20),2)
        for n in range(1,26):
            t=n/26
            line([(850+194*t,672+35*t),(791+184*t,736+39*t)],mix(wall,palette.warn,.23))
        for n in range(1,18):
            t=n/18
            line([(850-59*t,672+64*t),(1044-69*t,707+68*t)],mix(wall,palette.warn,.17))
        # Contact shadows and coherent lamp pools are static and blurred once.
        # These low-frequency layers need no supersampling. Keep the geometry
        # pass crisp and upsample only the soft light after its small blur.
        soft_size=(max(1,image.width//4),max(1,image.height//4))
        lx,ly=soft_size[0]/1600,soft_size[1]/1000
        shade=Image.new('RGBA',soft_size); sd=ImageDraw.Draw(shade)
        for x,y,rx,ry in [(207,474,120,24),(592,557,140,30),(298,714,140,28),(955,722,130,40)]:
            sd.ellipse(((x-rx)*lx,(y-ry)*ly,(x+rx)*lx,(y+ry)*ly),fill=(0,0,0,80))
        image.alpha_composite(shade.filter(ImageFilter.GaussianBlur(16*lx)).resize(image.size,Image.Resampling.BILINEAR))
        glow=Image.new('RGBA',soft_size); gd=ImageDraw.Draw(glow)
        warm=mix(palette.warn,palette.text,.35)
        gd.polygon([(1028*lx,533*ly),(1094*lx,729*ly),(838*lx,699*ly)],fill=(*warm,48))
        gd.ellipse((837*lx,592*ly,1080*lx,785*ly),fill=(*warm,25))
        gd.line((393*lx,130*ly,774*lx,184*ly),fill=(*warm,78),width=max(1,round(12*lx)))
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(35*lx)).resize(image.size,Image.Resampling.BILINEAR))
        draw=ImageDraw.Draw(image)
        # Plants, with fixed architectural positions and restrained silhouettes.
        for x,y in [(109,392),(310,269),(834,378),(835,651),(562,812)]:
            box(x,y,28,24,38)
            for j,(dx,dy) in enumerate([(-12,-46),(9,-65),(26,-46),(1,-34),(20,-28)]):
                leaf=mix(mix(palette.accent,palette.warn,.36),palette.entry,.64+j*.035)
                poly([(x+14,y-3),(x+dx-7,y+dy*.48),(x+dx-6,y+dy*.79),(x+dx,y+dy-8),(x+dx+10,y+dy*.74),(x+dx+12,y+dy*.48),(x+19,y-2)],leaf)
                line([(x+16,y-3),(x+dx+2,y+dy)],mix(leaf,palette.muted,.24))
    else:
        # Desks place their people behind the working surface.
        for x,y,bw,depth,height in [(115,395,185,79,85),(486,482,204,83,83),(179,621,230,78,87)]:
            box(x,y,bw,depth,height)
            box(x+65,y-64,64,10,43)
            poly([(x+70,y-57),(x+121,y-48),(x+121,y-23),(x+70,y-32)],mix(palette.accent,palette.entry,.82))
            box(x+88,y-19,9,9,22)
            box(x+70,y+4,44,20,4)
            poly([(x+150,y+9),(x+179,y+14),(x+194,y+2),(x+165,y-3)],mix(palette.text,palette.muted,.25))
            for n in range(3):
                line([(x+76,y-51+n*7),(x+109,y-45+n*7)],mix(palette.accent,palette.text,.15),1)
            # Key rows, paper edges, cup and pen stay static furniture.
            for n in range(3):
                line([(x+76,y+n*3),(x+104,y+5+n*3)],mix(palette.entry,palette.muted,.4))
            box(x+16,y-14,15,12,20)
            line([(x+24,y-17),(x+22,y-35)],mix(palette.text,palette.muted,.5),2)
            line([(x+153,y+9),(x+179,y+14)],palette.text,1)
        box(201,593,40,30,14)
        box(207,578,30,25,12)
        box(916,663,43,43,42)
        box(991,702,62,35,24)
        for offset in (18,44):
            line([(991+offset,702+offset*.18),(1015+offset,683+offset*.18)],mix(edge,palette.entry,.3),2)
        # The fixture has its own dark housing, with warm light underneath.
        box(1005,517,54,10,7)
        line([(1010,524),(1058,533)],mix(palette.warn,palette.text,.45),4)
    return image.resize((w,h),Image.Resampling.LANCZOS)


def employee(edge, visuals, variant=0, state="", character_style=None):
    """Four faceless volumes, with role-stable silhouette differences."""
    from PIL import Image, ImageDraw, ImageFilter
    from mo_desktop.design import CubeFormSpec
    from mo_desktop.settings import CharacterSettings
    if character_style is None:
        character_style = CharacterSettings()
    palette = visuals.palette
    brand = '#%02x%02x%02x' % tuple(visuals.token('_BRAND_RGB'))
    if character_style.color_mode != 'skin':
        brand = character_style.color_mode
    form = CubeFormSpec(positions=((48.,42.),(48.,110.),(39.,174.),(84.,182.)))
    s = 3
    image=Image.new('RGBA',(180*s,230*s))
    shadow=Image.new('RGBA',image.size)
    d=ImageDraw.Draw(shadow)
    d.ellipse((22*s,194*s,161*s,218*s),fill=(0,0,0,95))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5*s)))
    d=ImageDraw.Draw(image)
    def poly(points,color):
        # Round projected edges using the canonical character corner setting.
        points=[(round(x*s),round(y*s)) for x,y in points]
        radius = character_style.corner_radius
        stroke=max(1,round(radius*8*s))
        d.polygon(points,fill=color)
        d.line(points+[points[0]],fill=color,width=stroke,joint='curve')
    def cube(x,y,w,h,depth):
        front=[(x,y),(x+w,y+w*.23),(x+w,y+w*.23+h),(x,y+h)]
        side=[front[1],(x+w+depth,y+w*.23-depth*.64),(x+w+depth,y+w*.23+h-depth*.64),front[2]]
        top=[(x,y),(x+depth,y-depth*.64),side[1],front[1]]
        poly(front,mix(brand,palette.text,.12))
        poly(side,mix(brand,palette.card,.40))
        poly(top,mix(brand,palette.text,.38))
        d.line([(round(a*s),round(b*s)) for a,b in top[:2]+top[2:]+[top[0]]],fill=mix(brand,palette.text,.48),width=s//2)
    width=[55,48,62,43][variant%4]
    offset=[0,4,-5,2][variant%4]
    lean = -7 if state == 'blocked' else 4 if state == 'running' else 0
    stride = 7 if state == 'running' else -3 if state in ('offered','accepted') else 0
    pieces = [(offset+lean,width,55,25),(0,width-3,53,24),
              (-stride,24,28,18),(stride,25,27,18)]
    for (x,y),(shift,width,height,depth) in zip(form.positions,pieces):
        cube(x+shift,y,width,height,depth)
    if state == 'completed':
        poly([(110,146),(140,151),(153,140),(124,135)],mix(palette.text,palette.muted,.15))
    glow = character_style.glow
    if glow > 0:
        bloom = image.filter(ImageFilter.GaussianBlur(form.glow_blur_ratio*55*s))
        bloom.putalpha(bloom.getchannel('A').point(lambda value: round(value*glow*.18)))
        bloom.alpha_composite(image)
        image = bloom
    return image.resize((round(edge*1.8),round(edge*2.3)),Image.Resampling.LANCZOS)
