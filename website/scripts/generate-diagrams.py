"""Generate the site's original, responsive SVG diagrams (standard library only)."""
from pathlib import Path
from html import escape
P=Path(__file__).resolve().parents[1]/'public'/'diagrams'; P.mkdir(parents=True,exist_ok=True)
INK='#193d36'; MUTED='#536d63'; TEAL='#086b6c'; DARK='#102d29'; PALE='#e6f2eb'; WHITE='#fbfdf9'
def text(x,y,value,size=14,fill=INK,weight=400,anchor='start',extra=''):
    return f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" {extra}>{escape(value)}</text>'
def rect(x,y,w,h,fill,stroke='none',r=8,extra=''):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" {extra}/>'
def path(d,stroke='#71978a',width=1.4,extra=''):
    return f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width}" {extra}/>'
def line(x1,y1,x2,y2,stroke='#71978a',extra=''):
    return path(f'M{x1} {y1}H{x2}' if y1==y2 else f'M{x1} {y1}L{x2} {y2}',stroke,1.3,extra)
def circle(x,y,r=3,fill='#b8e9ce'):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"/>'
def svg(name,w,h,title,desc,body,dark=False):
    defs='''<defs><pattern id="grid-NAME" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="COLOR" stroke-width=".6"/></pattern><linearGradient id="surface-NAME" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#e0f4e5"/><stop offset="1" stop-color="#90c6b0"/></linearGradient><linearGradient id="side-NAME" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#729f8b"/><stop offset="1" stop-color="#2f6256"/></linearGradient></defs>'''.replace('NAME',name).replace('COLOR','#25483e' if dark else '#d6e2d7')
    out=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-labelledby="art-title-{name} art-desc-{name}" style="font-family:Pretendard,'Malgun Gothic','Apple SD Gothic Neo',sans-serif"><title id="art-title-{name}">{escape(title)}</title><desc id="art-desc-{name}">{escape(desc)}</desc>{defs}{body}</svg>'''
    (P/(name+'.svg')).write_text(out,encoding='utf-8')
def card(x,y,w,h,title,sub,dark=False,tag=None):
    bg='#173c34' if dark else '#fff'; border='#436556' if dark else '#ceddd2'; fg='#e5f2e8' if dark else INK; small='#b4c9b9' if dark else MUTED
    return rect(x,y,w,h,bg,border)+text(x+18,y+29,title,16,fg,600)+text(x+18,y+52,sub,12,small)+(text(x+w-16,y+28,tag,10,small,anchor='end') if tag else '')
# Hero: a purpose-made isometric project fabric, not a product screenshot.
for compact in [False,True]:
    name='project-fabric-mobile' if compact else 'project-fabric'; w,h=(360,450) if compact else (740,555)
    b=rect(0,0,w,h,DARK,r=0)+rect(0,0,w,h,f'url(#grid-{name})',r=0)
    b+=text(24,32,'PROJECT FABRIC',10,'#b1cbb9',500,extra='letter-spacing="2"')+text(w-24,32,'01 / IEUM',10,'#b1cbb9',anchor='end')
    if compact:
        b+=card(20,59,145,64,'RFP','사업의 요구사항',True)+card(195,59,145,64,'TEMPLATES','팀의 문서 스타일',True)
        b+=path('M92 123V153H180V175M268 123V153H180','#8bbda6',1.2)
        cx,cy=180,221; sx,sy=118,57
    else:
        b+=card(30,114,166,76,'RFP','요구사항 · 사업 범위',True,tag='IN')+card(30,262,166,76,'TEMPLATES','정성제안서 · 발표자료',True)
        b+=card(554,114,156,76,'PROPOSAL','제안 · 발표',True)+card(554,230,156,76,'DELIVERY','설계 · 구현 · 검증',True)+card(554,346,156,76,'OPERATIONS','배포 · 인수 · 운영',True)
        b+=path('M196 152H238V227H274M196 300H238V262H274M464 240H508V152H554M464 262H554M464 285H508V384H554','#8bbda6',1.3)
        for x,y in [(238,152),(238,300),(508,152),(508,384),(508,262)]: b+=circle(x,y,2.7)
        cx,cy=370,245; sx,sy=120,63
    # Three connected rhombus planes with a machined edge.
    for offset,fill in [(64,'#254e41'),(32,'#477765'),(0,f'url(#surface-{name})')]:
        yy=cy+offset
        b+=f'<path d="M{cx-sx} {yy}L{cx} {yy+sy}L{cx+sx} {yy}V{yy+12}L{cx} {yy+sy+12}L{cx-sx} {yy+12}Z" fill="url(#side-{name})" stroke="#65947d" stroke-width=".7"/>'
        b+=f'<path d="M{cx} {yy-sy}L{cx+sx} {yy}L{cx} {yy+sy}L{cx-sx} {yy}Z" fill="{fill}" stroke="#9fc9b0" stroke-width=".7"/>'
    b+=f'<g transform="translate({cx-46} {cy-23})"><rect x="6" y="5" width="35" height="35" rx="11" fill="none" stroke="#087c7f" stroke-width="8"/><rect x="52" y="5" width="35" height="35" rx="11" fill="none" stroke="#193d46" stroke-width="8"/><path d="M36 15C36 29 46 22 51 26Q56 30 56 33" fill="none" stroke="#087c7f" stroke-width="8" stroke-linecap="round"/></g>'
    if compact:
        b+=text(180,382,'하나의 프로젝트, 이어지는 기록',13,'#e3f1e7',500,'middle')+line(24,400,336,400,'#3b6050')+text(180,428,'REQ  /  TASK  /  REVIEW  /  HANDOFF',10,'#b9d2c1',anchor='middle',extra='letter-spacing=".8"')
    else:
        b+=text(370,402,'PROJECT IEUM',13,'#e3f1e7',500,'middle',extra='letter-spacing="2.4"')+text(370,427,'요구사항을 기준으로, 다음 작업으로.',12,'#b8ccbe',anchor='middle')
        b+=line(30,471,710,471,'#426452')+text(30,501,'CONNECTED RECORDS',10,'#b1cbb9',extra='letter-spacing="1"')
        for i,v in enumerate(['REQ','TASK','REVIEW','HANDOFF']): b+=text(323+i*92,501,v,11,'#d9ebe0',500)
        b+=text(30,531,'개념도 · 문서와 작업 기록을 잇는 구조',10,'#9cbda8')
    svg(name,w,h,'이음 프로젝트 연결 개념도','RFP와 팀 템플릿이 이음의 작업 기준이 되어 제안, 구축, 운영과 REQ·TASK·REVIEW·HANDOFF 기록으로 이어집니다.',b,True)
# Architecture: factual host -> Python management engine -> local project files.
for compact in [False,True]:
    name='architecture-mobile' if compact else 'architecture'; w,h=(360,902) if compact else (1120,595)
    b=rect(0,0,w,h,DARK,r=0)+rect(0,0,w,h,f'url(#grid-{name})',r=0)
    if not compact:
        b+=text(32,45,'01',12,'#a7c7b2')+text(65,45,'AI HOST',12,'#b8d2bf',500,extra='letter-spacing="1.3"')
        b+=card(265,22,258,70,'Codex','활성 세션에서 실행',True)+card(549,22,258,70,'Claude Code','활성 세션에서 실행',True)
        b+=path('M394 92V121H678V92M536 121V148','#9fcbb2',1.4)+text(833,63,'작업 수행 · 파일 제작 · 렌더링',12,'#c3d8c9')
        b+=rect(28,148,1064,222,'#1a4238','#668b73',12)+text(52,182,'02  PROJECT IEUM',13,'#d5eddb',600,extra='letter-spacing="1"')+text(1067,182,'SKILL + PYTHON',11,'#abc7b4',anchor='end')
        for i,(title,sub,foot) in enumerate([('업무 규칙','스킬 · 기본 양식 · 지침','SI / PRODUCT / API / MAINTENANCE'),('관리 엔진','입력 · 원장 · 실행 기록','inspect → apply → review → advance'),('검토와 단계 이동','변경 해시 · 근거 · 승인','근거가 바뀌면 다시 확인')]):
            x=52+i*348;b+=card(x,208,320,96,title,sub,True)+text(x+18,285,foot,10,'#a8cbb6')
        b+=text(52,342,'실제 작업은 호스트가 수행하고, 엔진은 상태·연결·검토 근거를 관리합니다.',13,'#bcd6c4')
        b+=path('M560 370V414M200 414H920M200 414V437M560 414V437M920 414V437','#8dbba1',1.4)
        b+=text(32,406,'03  LOCAL WORKSPACE',11,'#c2d8c8',500,extra='letter-spacing="1.2"')
        for x,title,sub in [(28,'자료와 산출물','inputs/ · templates/ · docs/ · proposal/'),(392,'진행 기준과 원장','project.json · registers/*.csv'),(756,'근거와 인수인계','state/ · HANDOFF.md')]: b+=card(x,437,336,86,title,sub,True)
        b+=text(32,566,'사용자의 프로젝트 폴더',12,'#b7cfbd')+text(1088,566,'설치 폴더와 고객 자료 분리  /  별도 상시 서버 없음',12,'#b7cfbd',anchor='end')
    else:
        b+=text(22,34,'01 / AI HOST',11,'#bdd8c4',500,extra='letter-spacing="1.2"')
        b+=card(22,53,150,72,'Codex','활성 세션',True)+card(188,53,150,72,'Claude Code','활성 세션',True)
        b+=path('M97 125V148H263V125M180 148V184','#9fcbb2')+text(180,173,'작업 수행 · 제작 · 렌더링',11,'#c3d8c9',anchor='middle')
        b+=rect(16,191,328,356,'#1a4238','#668b73',10)+text(34,222,'02 / PROJECT IEUM',12,'#d5eddb',600)
        for i,(title,sub) in enumerate([('업무 규칙','스킬 · 기본 양식 · 지침'),('관리 엔진','입력 · 원장 · 실행 기록'),('검토와 단계 이동','변경 해시 · 근거 · 승인')]):
            b+=card(34,243+i*88,292,72,title,sub,True)
        b+=text(34,529,'SKILL + PYTHON · 상태와 근거 관리',11,'#bdd6c4')+line(180,547,180,597,'#9fcbb2')
        b+=text(22,584,'03 / LOCAL WORKSPACE',11,'#bdd8c4',500)
        for i,(title,sub) in enumerate([('자료와 산출물','inputs/ · templates/ · docs/ · proposal/'),('진행 기준과 원장','project.json · registers/*.csv'),('근거와 인수인계','state/ · HANDOFF.md')]):b+=card(22,605+i*86,316,70,title,sub,True)
        b+=text(180,887,'프로젝트 폴더에 기록 · 별도 상시 서버 없음',11,'#bdd6c4',anchor='middle')
    svg(name,w,h,'프로젝트이음 실행 아키텍처','Codex 또는 Claude Code가 실제 작업을 수행합니다. 이음의 스킬과 Python 관리 엔진이 업무 규칙, 원장, 변경 근거와 단계 검토를 관리합니다. 자료, 산출물, project.json, registers 원장, state 기록과 HANDOFF.md는 고객 프로젝트 폴더에 저장됩니다. 별도 상시 서버는 포함하지 않습니다.',b,True)
# Target architecture: illustrative and technology-neutral; no invented file uploads/notifications.
for compact in [False,True]:
    name='target-system-mobile' if compact else 'target-system'; w,h=(360,798) if compact else (1120,542)
    b=rect(0,0,w,h,'#f3f7f0',r=0)+rect(0,0,w,h,f'url(#grid-{name})',r=0)
    def node(x,y,ww,hh,title,sub,cls='',label=None):
        v=f'<g class="{cls}">'+rect(x,y,ww,hh,'#fff','#b9ccbd',8,extra='class="node-box"')+text(x+16,y+29,title,16,INK,600)+text(x+16,y+51,sub,12,MUTED)
        if label:v+=text(x+ww-14,y+24,label,10,TEAL,500,'end')
        return v+'</g>'
    if not compact:
        b+=text(30,31,'ILLUSTRATIVE SYSTEM / 가상 고객 포털',11,MUTED,500,extra='letter-spacing="1"')+text(1090,31,'RFP 2.1–2.6',11,MUTED,anchor='end')
        b+=text(30,81,'이용자',12,MUTED)+node(30,103,200,77,'고객','자신의 문의 · 상태 확인','system-access')+node(30,206,200,77,'운영 담당자','분류 · 답변 · 상태 변경','system-access')+node(30,309,200,77,'방문자','FAQ · 공지 열람','system-data')
        b+=path('M230 142H278V151H332M230 245H278V151M230 348H264V426H716V348H744','#749787',1.5)
        b+=rect(312,62,342,352,'#e4eee2','#c0d1bf',10)+text(332,89,'APPLICATION / 서비스 경계',11,MUTED,500)
        b+=node(332,112,302,78,'고객 · 운영 화면','반응형 UI · 키보드 접근','system-access system-data')
        b+=path('M483 190V215',TEAL,1.7)+node(332,215,302,78,'인증 · 권한 검사','역할과 문의 소유자 확인','system-access',label='REQ-001')
        b+=path('M483 293V318',TEAL,1.7)+node(332,318,302,76,'문의 서비스','등록 · 목록 · 상세 · 상태','system-data')
        b+=path('M634 254H688V142H744M634 356H704V245H744','#749787',1.5)
        b+=text(744,81,'데이터 영역',12,MUTED)+node(744,103,346,77,'계정 · 역할','접근 범위를 판단하는 기준','system-access')+node(744,206,346,77,'문의 · 답변 · 처리 이력','자신의 문의만 조회할 수 있도록','system-data')+node(744,309,346,77,'FAQ · 공지','비로그인 사용자에게 공개','system-data')
        b+=rect(30,444,1060,68,'#e1ece0','#bfceba',8)+text(50,472,'DELIVERY',10,TEAL,600,extra='letter-spacing="1"')+text(50,495,'배포·복구 절차 / 운영·사용자 매뉴얼 / 접근권한 테스트 결과',13,INK)+text(1067,482,'RFP 2.6',11,MUTED,anchor='end')
    else:
        b+=text(20,29,'가상 고객 포털 / 목표 시스템 예시',12,MUTED,500)
        b+=node(20,50,152,73,'고객','내 문의 · 상태 확인','system-access')+node(188,50,152,73,'운영 담당자','분류 · 답변 · 변경','system-access')
        b+=path('M96 123V148H264V123M180 148V174','#749787')
        b+=rect(12,173,336,316,'#e4eee2','#c0d1bf',10)+text(30,199,'APPLICATION / 서비스 경계',11,MUTED,500)
        b+=node(30,215,300,70,'고객 · 운영 화면','반응형 UI · 키보드 접근','system-access system-data')+line(180,285,180,305,TEAL)
        b+=node(30,305,300,70,'인증 · 권한 검사','역할과 문의 소유자 확인','system-access',label='REQ-001')+line(180,375,180,395,TEAL)+node(30,395,300,70,'문의 서비스','등록 · 목록 · 상세 · 상태','system-data')
        b+=line(180,489,180,515,TEAL)+node(20,515,320,74,'계정 · 문의 · 처리 이력','권한 검사 후 허용된 데이터에 접근','system-access system-data')
        b+=node(20,615,320,70,'FAQ · 공지','방문자도 읽는 공개 정보','system-data')
        b+=rect(20,713,320,65,'#e1ece0','#bfceba',8)+text(36,739,'운영 인수',13,INK,600)+text(36,762,'배포·복구 절차 · 매뉴얼 · 테스트 결과',11,MUTED)
    svg(name,w,h,'가상 고객 포털 목표 시스템 개념 설계','설명용 설계 예시입니다. 고객과 운영 담당자는 반응형 화면과 인증·권한 검사를 거쳐 문의 서비스와 데이터에 접근합니다. 방문자는 FAQ와 공지를 읽습니다. 역할과 문의 소유자 검사가 REQ-001에 대응합니다. 기술 제품과 호스팅은 미확정입니다.',b)
print('Created 6 responsive SVG assets')
