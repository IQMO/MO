"""Native connected workroom; execution and conversation remain with their hosts."""
from __future__ import annotations
import threading
import time
from typing import Any
from .snapshot import build_snapshot, read_runtime_events


def open_role_workspace(agent: Any, roles: Any) -> str:
    state = getattr(agent, '_thread_state', None)
    companion = getattr(agent, '_companion', None)
    if companion is not None and getattr(state, 'surface_session_slot', '') == 'mo-desktop':
        ready = threading.Event()
        errors = []
        def show():
            try:
                if companion._gui is None or getattr(companion._active_skill_role, 'role', '') != 'project-architect':
                    raise RuntimeError('The Desktop role is no longer active')
                companion._role_workspace_roles = tuple(roles)
                companion._role_workspace_requested = True
                companion._open_project_role_workspace()
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                ready.set()
        companion._post_gui_call(show)
        if not ready.wait(5):
            return '[ROLE WORKSPACE PENDING] Desktop has not acknowledged the window yet.'
        if errors:
            return f'[ROLE WORKSPACE ERROR] Desktop could not open the window ({errors[0]}).'
    elif callable(getattr(agent, '_dashboard_dispatch', None)) and getattr(state, 'session', None) is None:
        try:
            reply = agent._dashboard_dispatch('role_workspace', '', roles=roles)
            if reply.get('message') != 'Role workspace opened':
                return '[ROLE WORKSPACE PENDING] Terminal has not acknowledged the window yet.'
        except Exception as exc:
            return f'[ROLE WORKSPACE ERROR] Native window unavailable ({type(exc).__name__}). Use /role status for the roster.'
    else:
        return '[ROLE WORKSPACE UNAVAILABLE] Open this role from a local MO Terminal or MO Desktop conversation.'
    return "[ROLE WORKSPACE OPEN] The native specialist view is connected to this conversation's real roster and workers. No project task was started."


class TerminalMologrthimHost:
    """Pump the native view on Terminal's existing UI loop."""
    def __init__(self, agent, roles, loop, on_submit=None):
        self.agent, self.session = agent, agent.session
        self.session_id = str(getattr(self.session, 'session_id', ''))
        self.project = str(agent._effective_project_cwd())
        self.roles, self.loop, self.on_submit = tuple(roles), loop, on_submit
        self.root = self.view = self._tick_handle = None
        self._refresh_at = self._observe_at = 0.
        self._closed = self._observing = False
        self._observation = {}
        self._event_cache = {}
        self.role_bound = self.can_talk = True
        from .connections import RoomConnections
        self.connections = RoomConnections(getattr(agent, 'config', {}), self.project, terminal=True)

    def matches(self, agent):
        return (not self._closed and agent.session is self.session
                and str(getattr(agent.session, 'session_id', '')) == self.session_id
                and str(agent._effective_project_cwd()) == self.project)

    def _snapshot(self):
        data = build_snapshot(self.roles, getattr(self.agent, 'workers', None),
            session=self.session, binding='Terminal conversation',
            board=getattr(self.agent, '_active_task_board', None),
            events=self._observation.get('events'), learning=self._observation.get('learning'),
            summary='Connected to this Terminal conversation.')
        data.update({key: value for key, value in self._observation.items() if key not in {'events', 'learning'}})
        return data

    def _submit(self, text):
        if not self.can_talk or not self.matches(self.agent):
            raise RuntimeError('The owning conversation has changed')
        self.on_submit(text)
        return {'accepted': True}

    def show(self):
        from mo_desktop.visuals import load_and_publish_desktop_visual_state
        load_and_publish_desktop_visual_state(getattr(self.agent, 'config', {}))
        if self.root is None:
            import tkinter as tk
            from core.runtime.instance import get_instance_id
            from interface.terminal_host import focus_terminal
            self.root = tk.Tk()
            self.root.withdraw()
            self.view = MologrthimWindow(self.root,self._snapshot,lambda:focus_terminal(get_instance_id()),
                on_dismiss=self.close, connections=self.connections)
        self.view.on_submit = self._submit if self.can_talk and self.on_submit else None
        self.view.show()
        if self._tick_handle is None:
            self._tick_handle=self.loop.call_later(.05,self._tick)

    def close(self):
        self._closed=True
        self.connections.close()
        if self._tick_handle is not None:
            self._tick_handle.cancel()
            self._tick_handle=None
        if self.view is not None:
            self.view.destroy()
            self.view=None
        if self.root is not None:
            self.root.destroy()
            self.root=None

    def _observe(self):
        observation = {}
        try:
            observation.update(self.connections.poll())
            from core.learning.status import build_learning_status
            from core.runtime.backend_monitor import get_monitor
            observation.update(events=read_runtime_events(get_monitor(),self.session_id,self._event_cache),
                learning=build_learning_status(getattr(self.agent,'profile',None),config=getattr(self.agent,'config',{})).as_dict())
        except Exception:
            pass
        finally:
            if self.matches(self.agent):
                self._observation=observation
            self._observing=False

    def _tick(self):
        self._tick_handle=None
        if not self.matches(self.agent) or (self.role_bound and getattr(self.agent._active_role(),'role','') != 'project-architect'):
            self.close()
            return
        try:
            visible=self.view.window.state()=='normal'
            now=time.monotonic()
            if visible and now>=self._observe_at and not self._observing:
                self._observe_at=now+2
                self._observing=True
                threading.Thread(target=self._observe,daemon=True).start()
            if visible and now>=self._refresh_at:
                from interface.theming import get_skin_name
                from mo_desktop.visuals import load_and_publish_desktop_visual_state
                if self.view._visuals.skin_id != get_skin_name():
                    self.view.apply_visual_state(load_and_publish_desktop_visual_state(getattr(self.agent,'config',{})))
                self.view.refresh()
                self._refresh_at=now+.3
                if self._closed:
                    return
            self.root.update()
            if self._closed:
                return
            self._tick_handle=self.loop.call_later((.016 if self.view._motion else .05) if visible else .5,self._tick)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Terminal role window closed after a GUI error')
            self.close()


class MologrthimWindow:
    """Cached architectural stage and a same-session native conversation panel."""
    def __init__(self,root,snapshot,on_conversation,on_dismiss=None,*,monitor_anchor=None,on_submit=None,connections=None):
        self.root,self.snapshot,self.on_conversation=root,snapshot,on_conversation
        self.on_dismiss=on_dismiss or (lambda:None)
        self.monitor_anchor,self.on_submit=monitor_anchor,on_submit
        self.window=self._canvas=self._visuals=None
        self._data={}
        self._selected=''
        self._page=0
        self._bitmaps={}
        self._character_style=None
        self._plate=self._furniture=self._plate_key=None
        self._motion=None
        self._motion_items=[]
        self._hits=[]
        self._drag=None
        self._inspector=None
        self._chat=self._composer=None
        self._chat_key=None
        self._receipt=''
        self._activity_cues=[]
        self.connections = connections
        self.landing = False
        self._terminal_page = 0
        self._connection_receipt = ''
        self._resource_text_items = []

    def show(self):
        from interface.desktop_ui import active_desktop_visual_state
        from interface.desktop_widgets import reveal_desktop_window
        from mo_desktop.settings import load_settings
        from mo_desktop.visuals import load_desktop_config
        style=load_settings(load_desktop_config()).character
        if style!=self._character_style:
            self._character_style=style
            self._bitmaps.clear()
        visuals=active_desktop_visual_state()
        if self.window is None or not self.window.winfo_exists():
            self._visuals=visuals
            self._build()
        elif visuals!=self._visuals:
            self.apply_visual_state(visuals)
        self.refresh()
        reveal_desktop_window(self.window,focus=True)
        self._draw()

    def _build(self):
        import tkinter as tk
        from interface.desktop_widgets import centre_desktop_window,install_desktop_window_corners
        self.window=tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.title('Mologrthim')
        self.window.overrideredirect(True)
        self.window.geometry('1280x800')
        self.window.minsize(900,600)
        self.window.protocol('WM_DELETE_WINDOW',self._dismiss)
        self._canvas=tk.Canvas(self.window,highlightthickness=0,bg=self._visuals.palette.card)
        self._canvas.pack(fill='both',expand=True)
        self._canvas.bind('<Configure>',lambda e:self._draw())
        self._canvas.bind('<Button-1>',self._click)
        self._canvas.bind('<B1-Motion>',self._move)
        self._canvas.bind('<ButtonRelease-1>',lambda e:setattr(self,'_drag',None))
        self._canvas.bind('<Motion>',self._hover)
        self._canvas.bind('<MouseWheel>',self._scroll)
        self.window.bind('<Escape>',lambda e:self._dismiss())
        self.window.bind('<Tab>',self._next_role)
        self.window.bind('<Unmap>',lambda e:self._stop_motion())
        self._chat=tk.Text(self._canvas,wrap='word',relief='flat',borderwidth=0,highlightthickness=0,padx=3,pady=3,cursor='arrow',state='disabled')
        self._composer=tk.Entry(self._canvas,relief='flat',highlightthickness=0)
        self._composer.bind('<Return>',self._send)
        install_desktop_window_corners(self.window)
        centre_desktop_window(self.window,self.root,monitor_anchor=self.monitor_anchor)

    def _stop_motion(self):
        if self._motion is not None and self.window is not None:
            self.window.after_cancel(self._motion)
        self._motion=None
        self._activity_cues=[]
        if self._canvas is not None:
            self._canvas.delete('activity-cue')

    def hide(self):
        self._stop_motion()
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()

    def _dismiss(self):
        self.hide()
        self.on_dismiss()

    def _open_conversation(self):
        self.hide()
        self.on_conversation()

    def destroy(self):
        self._stop_motion()
        window,self.window=self.window,None
        if window is not None and window.winfo_exists():
            window.destroy()
        self._canvas=self._chat=self._composer=None
        self._chat_key=None
        self._plate=self._furniture=self._plate_key=None
        self._bitmaps.clear()
        # Hit actions close over canvas widgets. Release them on the GUI lane,
        # rather than leaving a Tk interpreter for a worker's cyclic GC.
        self._hits.clear()
        self._motion_items.clear()
        self._drag=None
        self._resource_text_items=[]

    def apply_visual_state(self,visuals):
        from interface.desktop_ui import DesktopVisualState
        if not isinstance(visuals,DesktopVisualState):
            raise TypeError('Mologrthim requires DesktopVisualState')
        self._visuals=visuals
        self._plate_key=None
        self._bitmaps.clear()
        if self._canvas is not None:
            self._canvas.configure(bg=visuals.palette.card)
            self._draw()

    def refresh(self):
        if self.window is None or not self.window.winfo_exists():
            return
        data=self.snapshot() or {}
        opened = data.get('connection_opened', '')
        if opened and opened != self._connection_receipt:
            self._connection_receipt = opened
            self._dismiss()
            return
        if data==self._data:
            return
        previous=self._data
        self._data=data
        if self.landing and previous:
            def choices(value):
                return ([tuple(row.get(key) for key in ('instance_id','pid','slot','cwd','intent'))
                         for row in value.get('terminals',[])], value.get('connection_message'), value.get('connection_pending'))
            if choices(previous)==choices(data):
                return
        elif (previous.get('resources') and data.get('resources') and self._resource_text_items
                and self._inspector!='resources'
                and previous['resources'].get('pressure')==data['resources'].get('pressure')
                and {k:v for k,v in previous.items() if k not in {'resources','terminals'}}
                    == {k:v for k,v in data.items() if k not in {'resources','terminals'}}):
            if self.window.state()=='normal':
                from .connections import resource_cards
                p=self._visuals.palette
                for (title,value),(label,line,state) in zip(self._resource_text_items,resource_cards(data['resources'])):
                    color={'pressure':p.error,'working':p.warn,'normal':p.accent}.get(state,p.muted)
                    self._canvas.itemconfigure(title,text=label)
                    self._canvas.itemconfigure(value,text=line,fill=color)
            return
        old={r['role']:r.get('state') for r in previous.get('roles',[])}
        cues=[]
        if previous and data.get('session_id') == previous.get('session_id'):
            known={str(e.get('id') or (e.get('type'),e.get('label'),e.get('detail'))) for e in previous.get('events',[])}
            for event in data.get('events',[]) if previous.get('events_ready') else ():
                identity=str(event.get('id') or (event.get('type'),event.get('label'),event.get('detail')))
                if identity not in known:
                    kind=event.get('type','')
                    area=('archive' if kind.startswith('memory_') else 'learning'
                          if kind.startswith('learning_') else 'board' if kind=='taskboard' else 'activity')
                    cues.append((area,event.get('label') or 'Receipt recorded'))
            if data.get('tasks')!=previous.get('tasks'):
                cues.append(('board','Taskboard updated'))
            if (previous.get('learning') is not None and data.get('learning')
                    and data.get('learning')!=previous.get('learning')):
                cues.append(('learning','Learning updated'))
        roles=data.get('roles',[])
        if self._selected not in [r['role'] for r in roles]:
            self._selected=roles[0]['role'] if roles else ''
            self._page=0
            if self._inspector=='role':
                self._inspector=None
        self._draw()
        changed={r['role'] for r in roles if old and old.get(r['role'])!=r.get('state')}
        if not self.landing and (changed or cues):
            self._start_motion(changed, cues)

    def _request_terminal(self, target=None):
        if self.connections and self.connections.request(target):
            self._receipt = 'Opening a new Terminal…' if target is None else "Requesting this Terminal's room…"
            self._data['connection_message'] = self._receipt
            self._draw()

    def _next_terminal_page(self):
        count = len(self._data.get('terminals', []))
        self._terminal_page = (self._terminal_page + 1) % max(1, (count + 3) // 4)
        self._draw()

    def _release_visuals(self):
        # Focus the host before discarding the room, without invoking any
        # Terminal stop/interrupt or process termination action.
        self.on_conversation()
        self.destroy()
        self.on_dismiss()

    def _select(self,role):
        self._selected=role
        roles=self._data.get('roles',[])
        self._page=next((i//4 for i,r in enumerate(roles) if r['role']==role),0)
        self._inspector='role'
        self._draw()
        self._start_motion({role})

    def _next_role(self,event=None):
        if self.landing:
            return 'break'
        if event is not None and event.widget is self._composer:
            return None
        roles=self._data.get('roles',[])
        if roles:
            i=next((i for i,r in enumerate(roles) if r['role']==self._selected),-1)
            self._select(roles[(i+1)%len(roles)]['role'])
        return 'break'

    def _next_page(self):
        roles=self._data.get('roles',[])
        self._page=(self._page+1)%max(1,(len(roles)+3)//4)
        if roles:
            self._select(roles[self._page*4]['role'])

    def _hover(self,event):
        self._canvas.configure(cursor='hand2' if any(a<=event.x<=c and b<=event.y<=d for a,b,c,d,_ in self._hits) else '')

    def _click(self,event):
        for a,b,c,d,action in reversed(self._hits):
            if a<=event.x<=c and b<=event.y<=d:
                action()
                return
        self._drag=(event.x_root-self.window.winfo_x(),event.y_root-self.window.winfo_y())

    def _move(self,event):
        if self._drag:
            self.window.geometry(f'+{event.x_root-self._drag[0]}+{event.y_root-self._drag[1]}')

    def _scroll(self,event):
        if not self.landing and event.x>self._canvas.winfo_width()*.73:
            self._chat.yview_scroll(-3 if event.delta>0 else 3,'units')

    def _show_chat(self):
        self._inspector=None
        self._draw()

    def _inspect_source(self,source):
        self._inspector=source
        self._draw()

    def _send(self,event=None):
        if self.landing or not self.on_submit or not self._data.get('connected'):
            return 'break'
        value=self._composer.get().strip()
        if not value:
            return 'break'
        try:
            receipt=self.on_submit(value)
            if isinstance(receipt,dict) and (receipt.get('ok') is False or receipt.get('accepted') is False):
                self._receipt=receipt.get('message') or 'Message not accepted.'
            else:
                self._composer.delete(0,'end')
                self._receipt='Sent to MO'
        except Exception:
            self._receipt='Message not sent. Draft retained.'
        self.refresh()
        self._draw()
        return 'break'

    def _start_motion(self,changed=None,cues=()):
        self._stop_motion()
        if self.window is None or self.window.state()!='normal':
            return
        self._motion_roles=changed if changed is not None else {self._selected}
        self._activity_cues=list(dict(cues).items())
        self._motion_started=time.monotonic()
        self._animate()

    def _animate(self):
        from mo_desktop.emotes import sample,thinking,alert,moody,poke
        self._motion=None
        if self.window is None or self.window.state()!='normal':
            return
        elapsed=time.monotonic()-self._motion_started
        self._canvas.delete('activity-cue')
        for item,x,y,index,state,role in self._motion_items:
            if role not in self._motion_roles:
                continue
            motion={'running':thinking,'blocked':moody,'completed':alert,'offered':poke,'accepted':poke}.get(state,poke)
            dx,dy,_,_=sample(motion,index,min(elapsed,1.2),1.2)
            self._canvas.coords(item,x+dx*12*self._scale,y+dy*12*self._scale)
        if elapsed<1.2 and self._activity_cues:
            from .scene import mix
            from interface.desktop_ui import DESKTOP_TYPOGRAPHY
            c=self._canvas
            sx,sy=c.winfo_width()/1600,c.winfo_height()/1000
            p=self._visuals.palette
            _,_,brightness,_=sample(poke,0,elapsed,1.2)
            strength=min(1.,max(0.,brightness/.45))
            for area,label in self._activity_cues:
                tx,ty={'archive':(983,305),'board':(589,302),'learning':(977,584),'activity':(670,505)}[area]
                tint=p.warn if area=='learning' else p.accent
                color='#%02x%02x%02x'%mix(p.card,tint,strength)
                c.create_line(635*sx,460*sy,tx*sx,ty*sy,fill=color,width=max(1,2*self._scale),dash=(3,7),tags='activity-cue')
                progress=elapsed/1.2
                px,py=635+(tx-635)*progress,460+(ty-460)*progress
                radius=(3+strength*3)*self._scale
                c.create_oval(px*sx-radius,py*sy-radius,px*sx+radius,py*sy+radius,fill=color,outline='',tags='activity-cue')
                c.create_oval((tx-17)*sx,(ty-17)*sy,(tx+17)*sx,(ty+17)*sy,outline=color,width=max(1,2*self._scale),tags='activity-cue')
                c.create_text(tx*sx,(ty-37)*sy,text=label,fill=color,anchor='s',width=220*sx,font=(DESKTOP_TYPOGRAPHY.mono[0],-max(9,round(14*self._scale))),tags='activity-cue')
        if elapsed<1.2:
            self._motion=self.window.after(16,self._animate)
        else:
            self._activity_cues=[]

    def _draw(self):
        from PIL import ImageTk,Image,ImageDraw
        from interface.desktop_brand import make_four_cube_icon,make_glyph_icon
        from interface.desktop_ui import DESKTOP_TYPOGRAPHY
        from hashlib import blake2s
        from .scene import room,employee,mix
        if self._canvas is None or self.window is None or self.window.state()!='normal':
            return
        c=self._canvas
        w,h=c.winfo_width(),c.winfo_height()
        if w<10 or h<10:
            return
        p=self._visuals.palette
        sx,sy=w/1600,h/1000
        self._scale=min(sx,sy)
        c.delete('all')
        self._hits=[]
        self._motion_items=[]
        self._resource_text_items=[]
        key=(w,h,self._visuals)
        if key!=self._plate_key:
            self._bitmaps.clear()
            self._plate=ImageTk.PhotoImage(room((w,h),p),master=c)
            self._furniture=ImageTk.PhotoImage(room((w,h),p,foreground=True),master=c)
            self._plate_key=key
        c.create_image(0,0,image=self._plate,anchor='nw')
        def bitmap(key,paint):
            if key not in self._bitmaps:
                self._bitmaps[key]=ImageTk.PhotoImage(paint(),master=c)
            return self._bitmaps[key]
        def text(x,y,value,size=20,color=None,width=None,anchor='nw'):
            return c.create_text(x*sx,y*sy,text=str(value),fill=color or p.text,anchor=anchor,tags='scene-text',
                font=(DESKTOP_TYPOGRAPHY.mono[0],-max(9,round(size*self._scale))),width=width*sx if width else 0)
        role_hits=[]
        def hit(box,action,*,role=False):
            (role_hits if role else self._hits).append((box[0]*sx,box[1]*sy,box[2]*sx,box[3]*sy,action))
        def panel(box,fill=None,radius=None,outline=None):
            x,y,x2,y2=box
            pw,ph=max(1,round((x2-x)*sx)),max(1,round((y2-y)*sy))
            r=(self._visuals.metrics.panel_corner_radius if radius is None else radius)*self._scale
            def paint():
                plate=Image.new('RGBA',(pw*3,ph*3))
                ImageDraw.Draw(plate).rounded_rectangle((0,0,pw*3-1,ph*3-1),radius=r*3,fill=fill or p.card,outline=outline or p.border,width=3)
                return plate.resize((pw,ph),Image.Resampling.LANCZOS)
            return c.create_image(x*sx,y*sy,image=bitmap(('panel',pw,ph,r,fill,outline),paint),anchor='nw')
        def button(box,label,action):
            panel(box,p.entry,self._visuals.metrics.button_corner_radius)
            text((box[0]+box[2])/2,(box[1]+box[3])/2,label,16,anchor='center')
            hit(box,action)
        def character(x,y,size,variant=0,role='',state=''):
            variant%=4
            sprite=bitmap(('employee',size,variant,state),lambda:employee(size*self._scale,self._visuals,variant,state,self._character_style))
            item=c.create_image(x*sx,y*sy,image=sprite,anchor='s')
            self._motion_items.append((item,x*sx,y*sy,variant,state,role))
            if role:
                hit((x-size,y-size*2.3,x+size,y+45),lambda:self._select(role),role=True)
        icon=bitmap(('close',),lambda:make_glyph_icon('close',max(12,round(22*self._scale)),color=p.muted))
        text(128,200,'Mologrthim',25,p.muted)
        roles=self._data.get('roles',[])
        selection_mark=None
        positions=[(255,405,48),(307,624,53),(904,403,44),(885,681,49)]
        for i,row in enumerate(roles[self._page*4:self._page*4+4]):
            x,y,size=positions[i]
            state=row.get('state','')
            variant=blake2s(row['role'].encode(),digest_size=1).digest()[0]%4
            character(x,y,size,variant,row['role'],state)
            lx,ly=[(145,276),(203,716),(857,409),(840,710)][i]
            name=row.get('name') or row['role']
            text(lx,ly,name[:22],19,width=255)
            status={'offered':'Queued','accepted':'Queued','running':'Running','completed':'Report received','blocked':'Blocked','paused':'Paused','cancelled':'Stopped'}.get(state,'Available')
            text(lx,ly+28,status,15,p.error if state=='blocked' else p.accent if state in ('running','completed') else p.muted)
            hit((lx,ly,lx+245,ly+53),lambda r=row['role']:self._select(r),role=True)
            if self._inspector=='role' and row['role']==self._selected:
                selection_mark=(lx,ly)
        character(611,484,59,2)
        c.create_image(0,0,image=self._furniture,anchor='nw')
        c.tag_raise('scene-text')
        if selection_mark:
            lx,ly=selection_mark
            marker=panel((lx-12,ly+2,lx-8,ly+49),p.accent,
                         self._visuals.metrics.button_corner_radius,outline=p.accent)
            c.addtag_withtag('employee-selection',marker)
        text(597,579,'MO',25,anchor='n')
        count=self._data.get('active_count',0)
        desk_status=f'{count} active assignment' + ('s' if count!=1 else '') if count else 'Open conversation'
        text(597,614,desk_status,17,p.accent,anchor='n')
        hit((480,355,735,661),self._show_chat if self._inspector else self._open_conversation)
        brand=bitmap(('brand',),lambda:make_four_cube_icon(max(24,round(48*self._scale)),palette=p))
        c.create_image(581*sx,549*sy,image=brand)
        if not roles:
            text(140,290,'No specialists registered',17,p.muted,240)
        hit((366,109,808,354),lambda:self._inspect_source('tasks'))
        for j,(label,states) in enumerate([('Next',('pending',)),('In progress',('blocked','active')),('Finished',('completed','cancelled'))]):
            x,y=397+j*132,158+j*19
            rows=[r for r in self._data.get('tasks',[]) if r.get('status') in states]
            text(x,y,label,15)
            # Keep a blocker visible even when earlier active rows exist.
            rows.sort(key=lambda row:states.index(row['status']))
            if rows:
                row=rows[0]
                status=row['status']
                tint=p.error if status=='blocked' else p.accent if status=='active' else p.border
                panel((x-5,y+38,x+113,y+111),mix(p.card,tint,.09),
                      radius=self._visuals.metrics.button_corner_radius,outline=tint)
                text(x+4,y+44,{'blocked':'Blocked','active':'Working','completed':'Complete','cancelled':'Stopped','pending':'Pending'}[status],11,tint if status in ('blocked','active') else p.muted,102)
                text(x+4,y+64,row['title'][:34],13,p.text,102)
                if len(rows)>1:
                    text(x,y+118,f'+{len(rows)-1} more',11,p.muted)
            else:
                text(x,y+48,'No rows',13,p.muted)
        events=self._data.get('events',[])
        memory=next((e for e in reversed(events) if e.get('type','').startswith('memory_')),None)
        text(880,181,'Archive',20)
        text(855,215,'Receipt recorded' if memory else 'No session receipt',13,p.accent if memory else p.muted,235)
        if memory:
            hit((844,174,1110,399),lambda:self._inspect_source('archive'))
        text(875,488,'Learning',20)
        learning=self._data.get('learning')
        for j,(label,key) in enumerate([('Pending','pending_suggestions'),('Adopted','confirmed_suggestions'),('Memory','memory_turns')]):
            x,y=857+j*79,535+j*12
            note=mix(p.warn,p.text,.35)
            panel((x,y,x+72,y+75),note,radius=self._visuals.metrics.button_corner_radius,outline=mix(p.warn,p.card,.20))
            text(x+9,y+10,learning.get(key,0) if learning else '—',24,p.entry)
            text(x+5,y+48,label,12,p.entry)
        text(857,635,'Profile counts' if learning else 'Status unavailable',13,p.muted)
        if learning:
            hit((838,476,1109,661),lambda:self._inspect_source('learning'))
        panel((1162,79,1570,842))
        heading='Conversation'
        body=''
        if self._inspector=='role':
            selected=next((r for r in roles if r['role']==self._selected),None)
            if selected:
                heading=selected.get('name') or selected['role']
                body='\n\n'.join(label+'\n'+str(value) for label,value in [('Focus',selected.get('focus','')),('Assignment',selected.get('assignment') or 'No recorded assignment.'),('Status',selected.get('status','')),('Note',selected.get('note','')),('Evidence','\n'.join(selected.get('evidence',[])) or 'No evidence recorded.'),('Report',selected.get('report') or 'No report received.')])
        elif self._inspector=='tasks':
            heading='Taskboard'
            body='\n\n'.join('\n'.join(filter(None,[r['status']+' · '+r['title'],r.get('blocker',''),*r.get('evidence',[])])) for r in self._data.get('tasks',[])) or 'No taskboard rows in this conversation.'
        elif self._inspector=='archive':
            heading='Latest archive receipt'
            body=(memory.get('label','')+'\n\n'+memory.get('detail','')) if memory else 'No receipt in the current observation.'
        elif self._inspector=='learning':
            heading='Learning · profile'
            body=('Durable profile counts; pending suggestions require review.\n\n'+'\n'.join(f'{label}: {learning.get(key,0)}' for label,key in [('Pending','pending_suggestions'),('Adopted','confirmed_suggestions'),('Memory turns','memory_turns'),('Promoted workflows','promoted_workflows')])) if learning else 'Status unavailable.'
        elif self._inspector=='activity':
            heading='Recorded activity'
            body='\n\n'.join(e.get('label',e.get('type',''))+'\n'+e.get('detail','') for e in events) or 'No correlated receipts in the current observation.'
        elif self._inspector=='resources':
            from .connections import resource_text
            heading='Resources'
            body=resource_text(self._data.get('resources'))
        else:
            body='\n\n'.join(('You' if r['role']=='user' else 'MO')+'\n'+r['text'] for r in self._data.get('conversation',[]))
            if not body:
                body='Your existing MO conversation appears here.' if self._data.get('connected') else 'Conversation source unavailable.'
        text(1190,109,heading,25,width=350)
        text(1190,152,self._data.get('binding') or 'Source unavailable',14,p.accent,width=340)
        c.create_line(1183*sx,184*sy,1548*sx,184*sy,fill=p.border)
        self._chat.configure(bg=p.card,fg=p.text,insertbackground=p.accent,font=(DESKTOP_TYPOGRAPHY.mono[0],-max(10,round(19*self._scale))))
        chat_key=(self._inspector,body)
        if chat_key!=self._chat_key:
            tail=self._chat.yview()[1]>=.98
            old=self._chat.yview()[0]
            switched=self._chat_key is None or self._chat_key[0]!=self._inspector
            self._chat.configure(state='normal')
            self._chat.delete('1.0','end')
            self._chat.insert('1.0',body)
            self._chat.configure(state='disabled')
            if not self._inspector and (tail or switched):
                self._chat.see('end')
            elif switched:
                self._chat.yview_moveto(0)
            else:
                self._chat.yview_moveto(old)
            self._chat_key=chat_key
        self._chat.place(x=1188*sx,y=211*sy,width=356*sx,height=455*sy)
        if self._inspector:
            button((1190,680,1546,720),'Back to conversation',self._show_chat)
        else:
            text(1190,697,self._data.get('connection_message') or self._receipt or 'Same conversation · existing permissions',13,p.muted,340)
        if self.on_submit:
            panel((1183,761,1548,824),p.entry)
            self._composer.configure(bg=p.entry,fg=p.text,insertbackground=p.accent,font=(DESKTOP_TYPOGRAPHY.mono[0],-max(10,round(18*self._scale))),state='normal' if self._data.get('connected') else 'disabled')
            self._composer.place(x=1196*sx,y=780*sy,width=285*sx,height=27*sy)
            text(1190,737,'Talk to MO',13,p.muted)
            send=bitmap(('send',),lambda:make_glyph_icon('send',max(12,round(22*self._scale)),color=p.accent))
            c.create_image(1515*sx,791*sy,image=send)
            hit((1490,762,1548,824),self._send)
        else:
            self._composer.place_forget()
            text(1190,766,'Observing this Terminal',16,p.accent,340)
            text(1190,796,'Work continues independently',13,p.muted,340)
        panel((30,864,1570,945))
        c.create_image(92*sx,904*sy,image=brand)
        c.create_line(157*sx,881*sy,157*sx,928*sy,fill=p.accent)
        summary=(events[-1].get('label') or events[-1].get('type')) if events else self._data.get('summary') or self._data.get('activity') or 'Connected to the existing MO conversation.'
        text(193,889,summary,18,width=1080)
        if events:
            hit((180,870,1290,937),lambda:self._inspect_source('activity'))
        button((1320,882,1553,930),'Show Terminal' if self.connections and self.connections.terminal else 'Open conversation',self._open_conversation)
        text(31,967,'Mologrthim · connected workroom',13,p.muted)
        if len(roles)>4:
            button((928,705,1120,750),'Next employees',self._next_page)
        self._hits.extend(role_hits)
        resources=self._data.get('resources')
        if resources:
            from .connections import resource_cards
            panel((35,774,1125,842))
            for i,(label,line,state) in enumerate(resource_cards(resources)):
                x=55+i*360
                color={'pressure':p.error,'working':p.warn,'normal':p.accent}.get(state,p.muted)
                title=text(x,786,label,14,p.muted,345)
                value=text(x,811,line,15,color,345)
                self._resource_text_items.append((title,value))
            hit((35,774,1125,842),lambda:self._inspect_source('resources'))
            if resources.get('pressure')=='pressure' or self._inspector=='resources':
                text(725,966,'System under pressure' if resources.get('pressure')=='pressure' else 'Release artwork; keep work running',13,p.warn if resources.get('pressure')=='pressure' else p.muted,350)
                button((1110,953,1553,991),'Show Terminal · close visuals' if resources.get('shared_terminal') else 'Close visuals',self._release_visuals)
        if self.landing:
            self._chat.place_forget()
            self._composer.place_forget()
            self._hits=[]
            self._motion_items=[]
            veil=bitmap(('landing-veil',w,h),lambda:Image.new('RGBA',(w,h),(*mix(p.card,p.card,0),190)))
            c.create_image(0,0,image=veil,anchor='nw')
            panel((455,232,1145,778),p.card)
            c.create_image(508*sx,285*sy,image=brand)
            text(551,265,'Choose a Terminal',27,width=550)
            text(551,307,'Observe its room, or use + to start talking.',15,p.muted,560)
            terminals=self._data.get('terminals',[])
            self._terminal_page=min(self._terminal_page,max(0,(len(terminals)-1)//4))
            for index,row in enumerate(terminals[self._terminal_page*4:self._terminal_page*4+4]):
                y=365+index*78
                panel((482,y,1118,y+66),p.entry)
                from pathlib import Path
                title=f"{Path(row.get('cwd') or '').name or 'MO Terminal'} · {row.get('instance_id','')}"
                text(505,y+10,title,19,p.text,570)
                detail=' '.join(str(row.get('intent') or row.get('slot') or 'Ready').split())
                text(505,y+39,detail[:66],14,p.muted,570)
                hit((482,y,1118,y+66),lambda target=row:self._request_terminal(target))
            if not terminals:
                text(505,416,'No running MO Terminals',21,width=575)
                text(505,463,'Start a fresh conversation with the + above.\nNothing runs until you send a message.',17,p.muted,560)
            message=self._data.get('connection_message') or self._receipt
            text(486,696,message or 'Current Terminals · observation only',14,p.muted,600)
            if len(terminals)>4:
                button((925,734,1118,764),'More Terminals',self._next_terminal_page)
        if self.connections:
            text(1468,28,'New conversation',13,p.muted,anchor='e')
            button((1481,7,1530,50),'+',self._request_terminal)
        c.create_image(1570*sx,28*sy,image=icon)
        hit((1540,0,1600,58),self._dismiss)
