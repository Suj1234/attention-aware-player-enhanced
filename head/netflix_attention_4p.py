"""
Netflix Attention Controller — 4-Person Edition
================================================
Weighted group attention: pause only if group_score < ATTENTION_THRESHOLD.
  group_score = (# of people looking) / (# detected faces)
  Default threshold 0.5 = majority rule.

Faces sorted left-to-right each frame: P1=leftmost … P4=rightmost.

CLI:
  python head/netflix_attention_4p.py
  python head/netflix_attention_4p.py --threshold 0.6
  python head/netflix_attention_4p.py --platform prime
"""

import cv2, mediapipe as mp, numpy as np, math, time, os, subprocess, sys, argparse
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import FaceLandmarkerOptions, FaceLandmarker
from collections import deque

CAMERA_INDEX=0; FILTER_LENGTH=10; YAW_THRESHOLD_DEG=25; PITCH_THRESHOLD_DEG=20
AUTO_CALIB_FRAMES=30; DIGITAL_ZOOM=2.0; AWAY_GRACE_SEC=1.5; BACK_GRACE_SEC=0.8
MIN_ABSENT_FOR_SEEK_SEC=2.0; REWIND_DISPLAY_SEC=2.0; MAX_FACES=4
HEAD_LANDMARKS={"left":234,"right":454,"top":10,"bottom":152,"front":1}
MODEL_PATH=os.path.join(os.path.dirname(os.path.abspath(__file__)),"face_landmarker.task")
_URL_FILTER="netflix.com"

def _nf_js(action):
    return (f"(function(){{try{{var v=document.querySelector('video');"
            f"var n=window.netflix||(typeof netflix!=='undefined'?netflix:null);"
            f"var pl=null;if(n){{try{{var vp=n.appContext.state.playerApp.getAPI().videoPlayer;"
            f"var ids=vp.getAllPlayerSessionIds();if(ids.length>0)pl=vp.getVideoPlayerBySessionId(ids[0]);"
            f"}}catch(e){{}}}}if(!pl&&!v)return 'ERROR:no video';"
            f"var _play=function(){{if(pl)pl.play();else v.play()}};"
            f"var _pause=function(){{if(pl)pl.pause();else v.pause()}};"
            f"var _getTime=function(){{return pl?pl.getCurrentTime():v.currentTime*1000}};"
            f"var _seek=function(m){{if(pl)pl.seek(m);else v.currentTime=m/1000}};"
            f"{action.replace('pl.play()','_play()').replace('pl.pause()','_pause()').replace('pl.getCurrentTime()','_getTime()').replace('pl.seek','_seek')}"
            f"}}catch(e){{return 'ERROR:'+e.message}}}})();")

_JS_PLAY=_nf_js("_play();return 'PLAYING';"); _JS_PAUSE=_nf_js("_pause();return 'PAUSED';")
_JS_GET_TIME=_nf_js("return 'TIME:'+_getTime();")

def _inject_mac(js,url_filter="netflix.com"):
    esc=lambda s:s.replace("\\","\\\\").replace('"','\\"')
    script=f'''tell application "Google Chrome"\nset r to "NO_TAB"\nrepeat with w in windows\nrepeat with t in tabs of w\ntry\nif (URL of t) contains "{url_filter}" then\nset r to execute t javascript "{esc(js)}"\nexit repeat\nend if\nend try\nend repeat\nend repeat\nreturn r\nend tell'''
    res=subprocess.run(["osascript","-e",script],capture_output=True,text=True)
    if res.returncode!=0: raise RuntimeError(res.stderr.strip())
    if res.stdout.strip()=="NO_TAB": raise RuntimeError(f"No tab: {url_filter}")
    return res.stdout.strip()

def _inject_win(js,url_filter="netflix.com"):
    import urllib.request,json; import websocket
    tabs=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tab=next((t for t in tabs if url_filter in t.get('url','').lower()),None)
    if not tab: raise RuntimeError(f"No tab: {url_filter}")
    ws=websocket.create_connection(tab['webSocketDebuggerUrl'])
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":js,"returnByValue":True}}))
    res=json.loads(ws.recv()); ws.close()
    return str(res.get('result',{}).get('result',{}).get('value','ERROR'))

def _inject(js):
    import platform
    return _inject_win(js,_URL_FILTER) if platform.system()=="Windows" else _inject_mac(js,_URL_FILTER)

def _play():
    try: print(f"[▶] {_inject(_JS_PLAY)}")
    except Exception as e: print(f"[Play failed] {e}")

def _pause():
    try: print(f"[⏸] {_inject(_JS_PAUSE)}")
    except Exception as e: print(f"[Pause failed] {e}")

def _get_time():
    try:
        r=_inject(_JS_GET_TIME); return float(r[5:]) if r.startswith("TIME:") else -1.0
    except: return -1.0

def _seek(ms):
    try: _inject(_nf_js(f"_seek({int(ms)});return 'OK';"))
    except Exception as e: print(f"[Seek failed] {e}")

def fmt(sec): return f"{int(sec)//60:02d}:{int(sec)%60:02d}"

def head_conf(yaw,pitch):
    return (max(0.,1.-abs(yaw)/YAW_THRESHOLD_DEG)+max(0.,1.-abs(pitch)/PITCH_THRESHOLD_DEG))/2.

class P:
    def __init__(self,label):
        self.label=label; self.cy=self.cp=0.; self.done=False
        self.ys=[]; self.ps=[]; self.ro=deque(maxlen=FILTER_LENGTH); self.rd=deque(maxlen=FILTER_LENGTH)
        self.looking=False; self.ok=False; self.conf=0.; self.ry=self.rp=180.
        self.yo=self.po=0.; self.in_frame=False; self.absent=None
        self.ls=0.; self.aw=0.

def pose(lms,x1,y1,x2,y2,s):
    def pt(i):
        l=lms[i]; cw,ch=x2-x1,y2-y1
        return np.array([x1+l.x*cw,y1+l.y*ch,l.z*cw])
    L,R,T,B,F=pt(234),pt(454),pt(10),pt(152),pt(1)
    ra=R-L; ra/=np.linalg.norm(ra)
    ua=T-B; ua/=np.linalg.norm(ua)
    fw=np.cross(ra,ua); fw/=np.linalg.norm(fw); fw=-fw
    c=(L+R+T+B+F)/5.; s.ro.append(c); s.rd.append(fw)
    ad=np.mean(s.rd,axis=0); ad/=np.linalg.norm(ad)
    ao=np.mean(s.ro,axis=0)
    xz=np.array([ad[0],0.,ad[2]]); xz/=max(np.linalg.norm(xz),1e-9)
    yr=math.acos(np.clip(np.dot([0.,0.,-1.],xz),-1.,1.))
    if ad[0]<0: yr=-yr
    yd=np.degrees(yr); yd=abs(yd) if yd<0 else (360-yd if yd<180 else yd); s.ry=yd
    yz=np.array([0.,ad[1],ad[2]]); yz/=max(np.linalg.norm(yz),1e-9)
    pr=math.acos(np.clip(np.dot([0.,0.,-1.],yz),-1.,1.))
    if ad[1]>0: pr=-pr
    pd=np.degrees(pr); pd=360+pd if pd<0 else pd; s.rp=pd
    s.yo=(s.ry+s.cy)-180.; s.po=(s.rp+s.cp)-180.
    s.conf=max(0.,min(1.,head_conf(s.yo,s.po)))
    s.ok=s.done and abs(s.yo)<=YAW_THRESHOLD_DEG and abs(s.po)<=PITCH_THRESHOLD_DEG
    s.looking=s.ok
    return ao,ad,L,R

def calib(s,frame,fw,fh):
    if s.done: return True
    s.ys.append(s.ry); s.ps.append(s.rp); n=len(s.ys)
    idx=int(s.label[1])-1; bx=idx*(fw//MAX_FACES); bw=fw//MAX_FACES
    cv2.rectangle(frame,(bx,fh-8),(bx+int(bw*n/AUTO_CALIB_FRAMES),fh-2),(50,200,255),-1)
    cv2.putText(frame,f"Calib {s.label} {n}/{AUTO_CALIB_FRAMES}",(bx+4,fh-12),cv2.FONT_HERSHEY_SIMPLEX,.35,(220,220,220),1,cv2.LINE_AA)
    if n>=AUTO_CALIB_FRAMES:
        s.cy=180.-float(np.mean(s.ys)); s.cp=180.-float(np.mean(s.ps)); s.done=True
        print(f"[{s.label}] Calibrated"); return True
    return False

COLS=[(50,220,80),(80,180,255),(255,200,50),(200,80,255)]

def draw_panel(frame,s,px,py,idx):
    pw,ph=190,130; ov=frame.copy()
    cv2.rectangle(ov,(px,py),(px+pw,py+ph),(15,15,25),-1); cv2.addWeighted(ov,.78,frame,.22,0,frame)
    border=(180,140,0) if not s.in_frame else (COLS[idx] if s.looking else (50,60,200))
    cv2.rectangle(frame,(px,py),(px+pw,py+ph),border,2)
    fx,fy=px+8,py+18
    cv2.putText(frame,s.label,(fx,fy),cv2.FONT_HERSHEY_SIMPLEX,.55,(210,210,210),2,cv2.LINE_AA); fy+=20
    if not s.in_frame:
        cv2.putText(frame,"NOT IN FRAME",(fx,fy),cv2.FONT_HERSHEY_SIMPLEX,.35,(30,170,220),1,cv2.LINE_AA)
    elif not s.done:
        cv2.putText(frame,"CALIBRATING",(fx,fy),cv2.FONT_HERSHEY_SIMPLEX,.35,(200,200,50),1,cv2.LINE_AA)
    else:
        col=COLS[idx] if s.looking else (50,60,200)
        cv2.putText(frame,"LOOKING" if s.looking else "AWAY",(fx,fy),cv2.FONT_HERSHEY_SIMPLEX,.50,col,2,cv2.LINE_AA); fy+=18
        bw=pw-16; cv2.rectangle(frame,(fx,fy),(fx+bw,fy+7),(40,40,60),-1)
        cv2.rectangle(frame,(fx,fy),(fx+int(bw*s.conf),fy+7),col,-1); fy+=18
        tot=max(s.ls+s.aw,1); pct=int(s.ls/tot*100)
        cv2.putText(frame,f"{pct}% attn",(fx,fy),cv2.FONT_HERSHEY_SIMPLEX,.30,(180,170,120),1,cv2.LINE_AA)

def draw_bar(frame,paused,gscore,seek_s,elapsed,thr,np_):
    h,w=frame.shape[:2]; ov=frame.copy()
    cv2.rectangle(ov,(0,h-40),(w,h),(15,15,25),-1); cv2.addWeighted(ov,.82,frame,.18,0,frame)
    ntxt="Player: PAUSED" if paused else "Player: PLAYING"; nc=(80,80,230) if paused else (50,230,80)
    cv2.putText(frame,ntxt,(12,h-22),cv2.FONT_HERSHEY_SIMPLEX,.50,nc,1,cv2.LINE_AA)
    col=(50,230,80) if gscore>=thr else (50,80,230)
    cv2.putText(frame,f"Group: {int(gscore*100)}% (need {int(thr*100)}%)",(w//2-110,h-22),cv2.FONT_HERSHEY_SIMPLEX,.44,col,1,cv2.LINE_AA)
    if seek_s>0: cv2.putText(frame,f"↩ {fmt(seek_s)}",(w-110,h-22),cv2.FONT_HERSHEY_SIMPLEX,.38,(30,200,255),1,cv2.LINE_AA)
    cv2.putText(frame,f"Session {fmt(elapsed)} [{np_} viewers] [q]=quit",(12,h-6),cv2.FONT_HERSHEY_SIMPLEX,.30,(100,100,100),1,cv2.LINE_AA)

def main():
    global _URL_FILTER
    parser=argparse.ArgumentParser()
    parser.add_argument("--threshold",type=float,default=0.5)
    parser.add_argument("--platform",default="netflix")
    args=parser.parse_args()
    THR=max(0.,min(1.,args.threshold))
    try:
        from platforms import PLATFORMS
        _URL_FILTER=PLATFORMS.get(args.platform,PLATFORMS["netflix"])["url_filter"]
    except: pass

    persons=[P(f"P{i+1}") for i in range(MAX_FACES)]
    ss=time.time(); paused=False; away_s=None; back_s=None
    seek_start=None; sbpool=0.; rw_until=0.; lt=time.time()

    if not os.path.exists(MODEL_PATH): print(f"[ERROR] Model missing: {MODEL_PATH}"); sys.exit(1)
    bopts=mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    opts=FaceLandmarkerOptions(base_options=bopts,output_face_blendshapes=False,
        output_facial_transformation_matrixes=False,num_faces=MAX_FACES,
        min_face_detection_confidence=.3,min_face_presence_confidence=.3,
        min_tracking_confidence=.3,running_mode=mp_vision.RunningMode.VIDEO)
    det=FaceLandmarker.create_from_options(opts)
    cap=cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened(): print("[ERROR] Camera"); sys.exit(1)

    print(f"=== 4-Person Attention Player | Platform: {args.platform.upper()} | Threshold: {int(THR*100)}% ===")
    fi=0
    while cap.isOpened():
        ret,frame=cap.read()
        if not ret: break
        frame=cv2.flip(frame,1); fh,fw=frame.shape[:2]
        if DIGITAL_ZOOM!=1.:
            cx,cy=fw//2,fh//2; cw,ch=int(fw/DIGITAL_ZOOM),int(fh/DIGITAL_ZOOM)
            x1,y1=max(cx-cw//2,0),max(cy-ch//2,0); x2,y2=min(x1+cw,fw),min(y1+ch,fh)
            df=cv2.resize(frame[y1:y2,x1:x2],(fw,fh),interpolation=cv2.INTER_LINEAR)
        else: x1=y1=0; x2,y2=fw,fh; df=frame
        mi=mp.Image(image_format=mp.ImageFormat.SRGB,data=cv2.cvtColor(df,cv2.COLOR_BGR2RGB))
        ts=int(cap.get(cv2.CAP_PROP_POS_MSEC)) or (fi*33)
        res=det.detect_for_video(mi,ts); fi+=1
        now=time.time(); dt=now-lt; lt=now; el=now-ss
        dfaces=res.face_landmarks
        if len(dfaces)>1: dfaces=sorted(dfaces,key=lambda l:l[HEAD_LANDMARKS["front"]].x)
        nf=len(dfaces)
        for i,s in enumerate(persons):
            wi=s.in_frame; s.in_frame=(i<nf)
            if s.in_frame and not wi: s.absent=None; s.ro.clear(); s.rd.clear()
            elif not s.in_frame and wi: s.absent=now; s.looking=False; s.ro.clear(); s.rd.clear()
        for i,s in enumerate(persons):
            if not s.in_frame: s.looking=False; s.ok=False; s.conf=0.; continue
            ao,ad,L,R=pose(dfaces[i],x1,y1,x2,y2,s); calib(s,frame,fw,fh)
            rc=(50,230,80) if s.ok else (50,80,230); hw=np.linalg.norm(R-L)/2
            re=ao-ad*(2.5*hw); cv2.line(frame,(int(ao[0]),int(ao[1])),(int(re[0]),int(re[1])),rc,2)
            for lm in dfaces[i]: cv2.circle(frame,(int(x1+lm.x*(x2-x1)),int(y1+lm.y*(y2-y1))),1,(40,100,40),-1)
        for s in persons:
            if s.looking: s.ls+=dt
            elif s.in_frame: s.aw+=dt
        aa=any(not s.in_frame for s in persons)
        if aa:
            if seek_start is None: seek_start=now
        else:
            if seek_start is not None:
                sbpool+=now-seek_start; seek_start=None
                if sbpool>=MIN_ABSENT_FOR_SEEK_SEC:
                    cm=_get_time()
                    if cm>=0: _seek(int(max(0,cm-sbpool*1000)))
                    rw_until=now+REWIND_DISPLAY_SEC; sbpool=0.
                else: sbpool=0.
                away_s=back_s=None
        pc=[s for s in persons if s.in_frame and s.done]; np_=len(pc)
        gs=sum(1 for s in pc if s.looking)/max(np_,1) if np_>0 else 1.
        if pc:
            if gs<THR:
                back_s=None
                if away_s is None: away_s=now
                elif now-away_s>=AWAY_GRACE_SEC and not paused: _pause(); paused=True
            else:
                away_s=None
                if back_s is None: back_s=now
                elif now-back_s>=BACK_GRACE_SEC and paused: _play(); paused=False
        irw=now<rw_until
        if irw: grey=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); frame=cv2.cvtColor(grey,cv2.COLOR_GRAY2BGR)
        pw=195
        for i,s in enumerate(persons):
            px=fw-(MAX_FACES-i)*pw-5
            if px>=0: draw_panel(frame,s,px,10,i)
        ss_=( now-seek_start+sbpool) if seek_start else sbpool
        draw_bar(frame,paused,gs,ss_,el,THR,np_)
        if irw:
            rt="REWINDING"; rs,rk=1.5,3
            (tw,th),_=cv2.getTextSize(rt,cv2.FONT_HERSHEY_SIMPLEX,rs,rk)
            rx,ry=(fw-tw)//2,(fh+th)//2
            cv2.rectangle(frame,(rx-12,ry-th-10),(rx+tw+12,ry+10),(0,0,0),-1)
            cv2.putText(frame,rt,(rx,ry),cv2.FONT_HERSHEY_SIMPLEX,rs,(0,0,255),rk,cv2.LINE_AA)
        cv2.imshow("Attention-Aware Player — 4 Person",frame)
        if cv2.waitKey(1)&0xFF==ord('q'): break
    det.close(); cap.release(); cv2.destroyAllWindows()
    print("\n── Summary ──")
    for s in persons:
        tot=s.ls+s.aw; pct=int(s.ls/tot*100) if tot>0 else 0
        print(f"  {s.label}  {fmt(tot)} | {pct}% attention")

if __name__=="__main__": main()
