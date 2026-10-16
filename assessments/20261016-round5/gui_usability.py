"""Program-driven GUI ergonomics checks; no native/manual UI claim."""
import json,sys,time,threading
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]))
import tkinter as tk
from toy_browser.app import gui
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  time.sleep(.25);self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<html><body>delayed local page</body></html>')
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
root=tk.Tk();root.withdraw();app=gui.TabBrowser(root);out={}
try:
 p=app.tabs[app.current];p.load_html('<html><body><div style="height:2000px">long</div></body></html>',w=400,h=200)
 frame=app.page_frames[app.current];cmd=frame._scroll.cget('command');p.scroll_to(0)
 root.tk.call(cmd,'moveto','0.5');out['scrollbar_drag']={'offset':p.yoffset,'max':p.scroll_max(),'expected_nonzero':True}
 p.scroll_to(100);root.tk.call(cmd,'scroll','-1','units');out['scrollbar_up']={'before':100,'after':p.yoffset,'expected_less_than':100}
 html='<!DOCTYPE html><html><body style="margin:0"><button id="b" style="width:100px;height:30px" onclick="document.getElementById(\'out\').textContent=\'CLICKED\'">Go</button><div id="out">SAFE</div></body></html>'
 p.load_html(html,w=400,h=200)
 def find(b):
  if b.dom is not None and b.dom.attr('id')=='b':return b
  for c in b.children:
   r=find(c)
   if r is not None:return r
 bx=find(p._layout_root);idx=p.hit_test_at(int(bx.rect.x+5),int(bx.rect.y+5));p.set_focus(idx,restate=False);app._activate_focused()
 out['enter_button']={'actual':p._dom_tree.find_by_id('out').text_content(),'expected':'CLICKED'}
 out['bindings']={'root_configure':root.bind('<Configure>'),'root_keypress':root.bind('<KeyPress>'),'label_mousewheel':frame._label.bind('<MouseWheel>'),'viewport_mousewheel':bool(frame._viewport.bind('<MouseWheel>'))}
 # A Tk timer scheduled before the normal navigation action records when
 # the GUI event loop is next able to run, including actual network/render.
 samples=[]
 for k in range(3):
  fired=[];t=time.perf_counter();root.after(1,lambda:fired.append((time.perf_counter()-t)*1000))
  app._open_url(f'http://127.0.0.1:{server.server_port}/');action=(time.perf_counter()-t)*1000;root.update()
  samples.append({'navigation_handler_ms':action,'pending_1ms_timer_actual_ms':fired[0]if fired else None})
 out['navigation_event_loop']=samples;out['viewport_after_navigation']=[p.width,p.height]
 presentation=[]
 for k in range(3):
  t=time.perf_counter();photo=app._bitmap_to_photopix(p.bitmap,app.current);presentation.append((time.perf_counter()-t)*1000)
 out['photoimage_conversion_ms']=presentation
finally:
 root.destroy();server.shutdown();server.server_close()
(D/'gui_usability.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(out,ensure_ascii=False,indent=2))
