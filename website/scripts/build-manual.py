from pathlib import Path
from html import escape
import json
import os

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Flowable, Image as RLImage
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
VERSION = json.loads((ROOT / 'plugin.json').read_text(encoding='utf-8'))['version']
MANUAL_DATE = '2026.11.03'
OUT = ROOT / f'output/pdf/프로젝트이음-사용자매뉴얼-{VERSION}.pdf'
(ROOT / 'tmp/pdfs').mkdir(parents=True, exist_ok=True)
OUT.parent.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('Malgun', os.environ.get('PDF_FONT_REGULAR', 'C:/Windows/Fonts/malgun.ttf')))
pdfmetrics.registerFont(TTFont('MalgunBold', os.environ.get('PDF_FONT_BOLD', 'C:/Windows/Fonts/malgunbd.ttf')))
pdfmetrics.registerFontFamily('Malgun', normal='Malgun', bold='MalgunBold', italic='Malgun', boldItalic='MalgunBold')
NAVY = colors.HexColor('#142A42')
TEAL = colors.HexColor('#087F8C')
INK = colors.HexColor('#263849')
MUTED = colors.HexColor('#5E7080')
PALE = colors.HexColor('#EEF5F6')
LINE = colors.HexColor('#D9E3E8')
W, H = A4
CW = W - 100

styles = {
    'body': ParagraphStyle('body', fontName='Malgun', fontSize=10, leading=16.5, textColor=INK, wordWrap='CJK', spaceAfter=9),
    'small': ParagraphStyle('small', fontName='Malgun', fontSize=8.5, leading=13, textColor=MUTED, wordWrap='CJK', spaceAfter=6),
    'h1': ParagraphStyle('h1', fontName='MalgunBold', fontSize=23, leading=31, textColor=NAVY, spaceAfter=16, wordWrap='CJK'),
    'h2': ParagraphStyle('h2', fontName='MalgunBold', fontSize=12.5, leading=19, textColor=TEAL, spaceBefore=11, spaceAfter=8, wordWrap='CJK'),
    'card': ParagraphStyle('card', fontName='Malgun', fontSize=9.5, leading=15, textColor=INK, wordWrap='CJK'),
    'code': ParagraphStyle('code', fontName='Malgun', fontSize=8.6, leading=15, textColor=NAVY, wordWrap='CJK'),
    'cell': ParagraphStyle('cell', fontName='Malgun', fontSize=9, leading=14, textColor=INK, wordWrap='CJK'),
    'headcell': ParagraphStyle('headcell', fontName='MalgunBold', fontSize=9, leading=14, textColor=colors.white, wordWrap='CJK'),
}
story = []
chapters = []

def p(text, style='body'):
    return Paragraph(text, styles[style])

def body(text):
    story.append(p(text))

def sub(text):
    story.append(p(text, 'h2'))

def card(label, text, code=False):
    label_p = p(escape(label), 'h2')
    content = p(escape(text).replace('\n', '<br/>'), 'code' if code else 'card')
    box = Table([[content]], colWidths=[CW])
    box.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), PALE), ('BOX', (0, 0), (-1, -1), .5, LINE),
                            ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14),
                            ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
    story.extend([KeepTogether([label_p, box]), Spacer(1, 10)])

def table(headers, rows, widths):
    cells = [[p(escape(str(v)), 'headcell') for v in headers]]
    cells.extend([[p(escape(str(v)).replace('\n', '<br/>'), 'cell') for v in row] for row in rows])
    t = Table(cells, colWidths=[CW*x for x in widths], repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                          ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F8FA')]),
                          ('LINEBELOW', (0, 0), (-1, -1), .4, LINE),
                          ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                          ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    story.extend([t, Spacer(1, 9)])

class Bookmark(Flowable):
    def __init__(self, name, title):
        super().__init__()
        self.name, self.title = name, title
        self.width = self.height = 0
    def draw(self):
        self.canv.bookmarkPage(self.name)
        self.canv.addOutlineEntry(self.title, self.name, 0)

def chapter(num, title, kicker):
    story.append(PageBreak())
    name = f'section-{num}'
    chapters.append((num, title, name))
    story.extend([Bookmark(name, title), p(kicker, 'small'), p(title, 'h1')])

REPO = 'https://github.com/Agics-hsjeong/project-lifecycle-agent'

# 01 - Cover
mark = RLImage(str(ROOT / 'website/public/brand/symbol.png'), width=64, height=32)
mark.hAlign = 'LEFT'
story.extend([mark, Spacer(1, 13)])
story.append(p('CODEX + CLAUDE CODE', 'h2'))
story.append(Spacer(1, 22))
story.append(Paragraph('프로젝트이음<br/>Project Ieum', ParagraphStyle('coverEN', fontName='MalgunBold', fontSize=34, leading=43, textColor=NAVY)))
story.append(Spacer(1, 25))
story.append(Paragraph('사용자 매뉴얼', ParagraphStyle('coverKO', fontName='MalgunBold', fontSize=28, leading=40, textColor=TEAL)))
story.append(Spacer(1, 24))
body('제안요청서와 자기 템플릿으로 시작하는<br/><b>제안 작성부터 개발·납품·유지보수까지</b>')
story.append(Spacer(1, 30))
table(['시작', '제작', '운영'], [['RFP · 사업 아이디어\n예전 제안서 · 발표자료', '정성제안서 · 발표\n기획 · 설계 · 구현', '검증 · 배포 · 납품\n변경 · 유지보수']], [.333, .334, .333])
story.append(Spacer(1, 28))
body('<b>사용자는 자료·목표·사업 결정을 전달합니다.</b><br/>에이전트가 폴더 생성, 분석, 산출물 제작, 검수와 기록 관리를 처리합니다.')
story.append(Spacer(1, 28))
body('대상: 처음 사용하는 실무자 · 제안 담당자 · 프로젝트 관리자')
story.append(p(f'v{VERSION} 베타  |  {MANUAL_DATE}  |  MIT License', 'small'))
story.append(p(f'<link href="{REPO}" color="#087F8C">GitHub 저장소 열기</link>', 'small'))

# 02 - Map
chapter(1, '먼저 전체 흐름을 확인하세요', 'GETTING STARTED / 01')
body('이 문서는 A사 고객 포털 사업을 예시로 설명합니다. 프로젝트 이름과 경로는 자신의 업무에 맞춰 바꾸세요. 템플릿이 없어도 RFP 분석과 초안 작성부터 시작할 수 있습니다.')
table(['순서', '사용자가 할 일', '에이전트가 처리할 일'], [
    ['1. 설치', '사용하는 AI에 한 번 설치', '설치 상태·실행 환경 점검'],
    ['2. 시작', '작업 폴더와 RFP·아이디어 전달', '프로젝트 생성·원본 보존·요구사항 분석'],
    ['3. 스타일', '예전 자료와 대표 스타일 지정', '원본 등록·시각·문체·구성 분석'],
    ['4. 제안', '실제 사업 사실과 결정 전달', '제안서·발표 제작·비교 검수·제출 점검'],
    ['5. 운영', '계약·변경·검수 정보 전달', '설계부터 유지보수까지 작업과 기록 관리'],
], [.15, .36, .49])
sub('필요한 페이지로 이동')
toc = [
    ('Codex 설치', 3), ('Claude Code 설치', 4), ('RFP·아이디어로 시작', 5),
    ('참고 템플릿 넣기', 6), ('스타일 분석과 선택', 7), ('제안 패키지 제작', 8),
    ('검수·승인·제출', 9), ('수주 후 프로젝트 관리', 10), ('개발·배포·유지보수 요청', 11),
    ('이어하기와 파일 위치', 12), ('업데이트·문제 해결', 13), ('요청문 요약·참고 링크', 14),
]
toc_rows = []
for i in range(0, len(toc), 2):
    row=[]
    for title, page in toc[i:i+2]:
        row.append(Paragraph(f'<link href="#section-{page-1}" color="#087F8C">{title}</link>  {page:02d}', styles['cell']))
    toc_rows.append(row)
t=Table(toc_rows, colWidths=[CW/2]*2)
t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
story.append(t)
sub('사용 전에 기억할 점')
body('일상 업무는 대화로 요청합니다. 셸 명령·JSON·CSV 편집을 직접 할 필요는 없습니다. 기능 테스트와 결과물 검수는 에이전트가 수행하며, 실제 예산·인력·계약·검수 판단은 사용자가 제공합니다.')

# 03 - Codex
chapter(2, 'Codex에 설치하기', 'ONE-TIME SETUP / 02')
body('Codex에 로그인한 상태에서 설치합니다. 저장소를 받는 Git과 내부 도구용 Python 3.10 이상 또는 호스트 제공 런타임이 필요합니다. 문서 제작·렌더링 도구는 호스트 환경에 따라 달라집니다.')
card('권장: Codex에 설치를 요청하세요', 'Agics-hsjeong/project-lifecycle-agent의 main 브랜치를\nmarketplace로 등록하고 project-lifecycle-agent 플러그인을 설치해줘.\n설치·활성화 상태와 Python 실행 환경도 확인해줘.')
body('설치 도구를 사용할 수 있는 환경에서는 에이전트가 처리합니다. 제품이 파일·설치 권한을 요청하면 해당 화면에서 허용해야 합니다.')
card('직접 설치할 때만: 터미널에 한 줄씩 입력', 'codex plugin marketplace add Agics-hsjeong/project-lifecycle-agent --ref main\ncodex plugin add project-lifecycle-agent@project-lifecycle-marketplace', code=True)
sub('설치 후')
body('새 채팅을 시작하거나 앱을 다시 엽니다. 앱의 플러그인 목록에서 project-lifecycle-marketplace의 설치·활성화 상태를 확인할 수 있습니다. 자동으로 스킬이 선택되지 않으면 아래 이름을 대화 입력창에서 호출합니다.')
card('명시적 스킬 호출', '$project-lifecycle', code=True)
body('codex 명령을 찾지 못하면 “CLI 위치와 설치 가능한 방법을 진단해줘”라고 요청하세요. 매 프로젝트마다 설치 명령을 반복하지 않습니다.')
body('이 플러그인은 GitHub 저장소를 marketplace로 직접 등록하는 방식입니다. 공식 디렉터리에 기본 등록된 항목이라는 의미는 아닙니다.')

# 04 - Claude
chapter(3, 'Claude Code에 설치하기', 'ONE-TIME SETUP / 03')
body('일반 Claude 웹 채팅이 아닌 <b>Claude Code</b>에서 사용합니다. 두 제품을 오가려면 Codex와 Claude Code에 각각 설치하세요.')
card('Claude Code 대화 입력창에 한 줄씩 입력', '/plugin marketplace add Agics-hsjeong/project-lifecycle-agent\n/plugin install project-lifecycle-agent@project-lifecycle-marketplace', code=True)
body('위 명령은 PowerShell이나 일반 터미널에 붙여 넣는 명령이 아닙니다. Claude Code의 대화 입력창에서 실행합니다. 설치 범위 선택 화면이 나오면 여러 프로젝트에서 개인적으로 쓸 경우 <b>User</b>를 선택합니다.')
sub('설치 후 새 세션 시작')
body('/plugin에서 설치·활성화 상태를 볼 수 있습니다. 새 세션을 시작한 뒤 프로젝트 작업을 요청하세요. 자동 선택되지 않으면 다음 스킬을 명시적으로 호출합니다.')
card('명시적 스킬 호출', '/project-lifecycle-agent:project-lifecycle', code=True)
sub('두 제품의 사용 방식')
table(['공통', '제품별'], [
    ['같은 프로젝트 폴더·관리 파일을 사용', '설치와 활성화는 각 제품에서 수행'],
    ['RFP·템플릿·산출물을 파일로 보존', '대화 기록은 자동으로 동기화되지 않음'],
    ['자연어로 같은 업무 요청 가능', '파일 접근과 외부 서비스 권한은 각 호스트 설정 적용'],
], [.5, .5])
body('고객 업무 폴더는 플러그인 설치 폴더와 분리합니다. 다음 페이지에서 업무용 폴더를 열고 첫 프로젝트를 시작합니다.')

# 05 - Init
chapter(4, 'RFP 또는 아이디어로 시작하기', 'PROJECT START / 04')
body('업무용 폴더, 예를 들어 “내 프로젝트”를 Codex 또는 Claude Code에서 엽니다. RFP 파일을 첨부하거나 에이전트가 읽을 수 있는 실제 경로를 알려주세요.')
card('외주/SI: A사 고객 포털 예시', 'Project Lifecycle Agent로 A사 고객 포털 사업을 시작해줘.\n현재 작업 폴더를 업무 공간으로 쓰고 식별자는 a-portal로 해줘.\n외주/SI 사업이고, 제안요청서는 [실제 RFP 파일 경로]에 있어.\n\n프로젝트와 참고 템플릿 폴더를 준비하고 원본을 등록해줘.\n사업 검토 초안, 요구사항, 평가기준, 제출 조건과 고객 질문을 정리해줘.\n미정인 예산·일정·실적은 가정 또는 확인 필요로 표시해줘.\n환경 확인, 파일 생성과 기록 관리는 네가 처리해줘.')
body('대괄호 부분은 실제 파일 위치로 바꿉니다. 첨부를 쓴다면 “첨부한 RFP”라고 요청합니다. 에이전트가 실제 접근 가능한 원본을 확보한 뒤 등록합니다.')
sub('시작하면 만들어지는 것')
body('<b>projects/a-portal/</b>에 원본과 입력 출처, 요구사항·작업 기록, 사업 검토 초안과 HANDOFF.md 등을 준비합니다. 요구사항에는 원문 페이지·절과 수용 기준을 연결합니다. 같은 이름의 기존 프로젝트는 덮어쓰지 않습니다.')
card('자체 서비스: RFP 없이 시작', 'Project Lifecycle Agent로 학원 상담·수강 신청 서비스를 시작해줘.\n자체 서비스 프로젝트이고 식별자는 academy-service야.\n대상은 소규모 학원이고 목표는 상담 누락과 신청 관리 시간 감소야.\n사업 가설, 사용자 문제, MVP 범위, 성공 지표와 실행 계획을 작성해줘.\n프로젝트 폴더와 관리 기록도 준비해줘.')
body('기본 방법론은 하이브리드입니다. “2주 스프린트 중심으로 운영해줘”처럼 원하는 방식도 전달할 수 있습니다. 자체 서비스의 제안 단계는 사업 실행안과 내부·투자 발표로 조정합니다.')

# 06 - Templates
chapter(5, '자기 템플릿을 넣는 위치', 'REFERENCE TEMPLATES / 05')
body('프로젝트가 준비되면 과거 제안서와 발표자료의 <b>사본</b>을 아래 폴더에 넣습니다. 빈 양식을 새로 만들 필요는 없습니다. 정성제안서와 발표자료를 나눠 보관하세요.')
card('A사 고객 포털의 참고 폴더', '내 프로젝트/projects/a-portal/templates/\n\nqualitative/\n    공공기관_정성제안서.docx\n    플랫폼구축_정성제안서.pdf\n\npresentation/\n    플랫폼구축_제안발표.pptx\n    간결한스타일_발표.pdf', code=True)
table(['자료', '권장 원본', '그 밖의 참고 형식'], [
    ['정성제안서', 'DOCX', 'DOTX, PDF, HWP, HWPX, PNG/JPG/JPEG/WebP'],
    ['발표자료', 'PPTX', 'POTX, PDF, PNG/JPG/JPEG/WebP'],
], [.23, .19, .58])
body('DOCX/PPTX는 원본 구조를 유지한 작업 사본을 만드는 데 유리합니다. PDF·HWP·이미지는 호스트의 문서·OCR·시각 도구로 분석해 재구성합니다. 편집 가능한 원본을 똑같이 복원하는 기능은 아닙니다. DOTX/POTX 변환도 호스트 도구 지원에 따릅니다.')
sub('이미 자료 폴더가 있다면')
body('파일을 직접 옮기지 않고 “이 기존 폴더를 참고 자료로 등록해줘”라고 경로를 알려줘도 됩니다. 에이전트가 버전별 원본 사본을 보존합니다.')
body('회사 공통 자료는 작업 공간의 reference-templates/qualitative/와 reference-templates/presentation/를 준비해 달라고 요청한 뒤 넣습니다. 필요한 자료를 각 프로젝트에 등록해 사용합니다. 고객 자료를 플러그인 설치 캐시나 blueprint/에 넣지 마세요.')

# 07 - Style
chapter(6, '스타일과 작성 노하우 분석하기', 'STYLE ANALYSIS / 06')
card('자료를 넣은 후 보내는 요청', 'a-portal의 templates 폴더에 예전 제안서와 발표자료를 넣었어.\n원본을 등록하고 문서별 디자인과 작성 방식을 분석해줘.\n\n정성제안서는 공공기관_정성제안서.docx,\n발표자료는 플랫폼구축_제안발표.pptx를 대표 스타일로 사용해줘.\n서체·색상·여백·표·도식, 목차 전개와 핵심 메시지,\n근거 제시 방식과 페이지·슬라이드 구성 패턴을 정리해줘.\n이전 고객명·가격·인력·실적은 새 사업의 사실로 사용하지 마.')
table(['분석 영역', '에이전트가 확인할 내용'], [
    ['시각 스타일', '서체·색상·여백·표, 표지·본문·도식·증빙 페이지의 배치'],
    ['문체·설득 흐름', '제목 표현, 메시지 전개, 문제와 해법, 근거·실적 제시 방식'],
    ['편집 구조', 'DOCX 스타일·섹션·표, PPTX 테마·마스터·레이아웃·슬라이드 순서'],
    ['교체 대상', '이전 고객·가격·일정·인력·실적, 노트·주석·메타데이터'],
], [.25, .75])
sub('여러 스타일을 넣어도 됩니다')
body('대표 자료를 지정하지 않았고 후보가 여러 개면 에이전트가 차이를 정리하고 선택을 요청합니다. 정성제안서와 발표자료는 서로 다른 대표 자료를 선택할 수 있습니다. 새 파일이 추가되었다고 기존 선택이 자동으로 바뀌지는 않습니다.')
sub('분석은 프로젝트에 남습니다')
body('원본, 선택, 스타일·문체·구성 규칙과 비교 기록은 templates/ 아래에 보존합니다. 같은 프로젝트 폴더를 열면 다음 세션에도 활용합니다. 모델을 별도로 학습시키는 방식은 아닙니다.')
body('자동 구조 관측만으로 시각 분석을 완료했다고 판단하지 않습니다. 에이전트가 실제 렌더링된 예시를 확인하고 제작 결과와 비교해야 합니다.')

# 08 - Proposal
chapter(7, '정성제안서·발표자료 제작하기', 'PROPOSAL PACKAGE / 07')
body('RFP의 지정 목차·평가기준·제출 형식을 먼저 적용하고, 선택한 과거 자료의 디자인과 작성 패턴을 사용하도록 요청합니다.')
card('제안 패키지 제작 요청', 'a-portal의 RFP와 선택한 템플릿 분석 결과로 제안 패키지를 만들어줘.\nRFP 지정 목차와 평가기준을 우선하고 대표 자료의 스타일을 적용해줘.\n\n정성제안서, 공수·일정 산정서, 평가항목 대응표, 발표자료,\n발표 대본·예상 Q&A, 제출 점검표를 작성해줘.\n발표 시간은 RFP를 확인하고 규정이 없으면 15분 초안으로 표시해줘.\n편집 가능한 DOCX·PPTX와 제출용 PDF를 가능한 도구로 제작해줘.\n전체 페이지·슬라이드를 렌더링하고 원본과 비교해 문제를 수정해줘.\n평가항목별 제안서 절·슬라이드·증빙 연결도 검사해줘.\n완료 파일 경로, 검수 결과와 아직 확인할 사업 결정을 알려줘.')
table(['결과물', '목적'], [
    ['정성제안서', '사업 이해와 수행·기술·관리·운영 전략'],
    ['공수·일정 산정서', '범위·인력·일정과 산정 근거'],
    ['평가항목 대응표', '평가기준 → 제안서 절 → 슬라이드 → 증빙 연결'],
    ['발표자료', '발표 흐름과 실제 슬라이드'],
    ['대본·예상 Q&A', '발표 노트·시간 배분·질의응답 준비'],
    ['제출 점검표', '필수 파일·형식·버전·누락 확인'],
], [.3, .7])
body('에이전트가 실제 결과 파일의 경로를 안내합니다. 업무 문서는 docs/, 제작 파일은 생성 시 마련하는 exports/ 등에서 관리합니다. 발표가 없는 사업은 실제 근거와 제외 사유를 기록합니다.')

# 09 - QA
chapter(8, '검수·승인·제출 구분하기', 'QUALITY & DECISIONS / 08')
body('작성 요청에는 <b>실제 파일 제작과 렌더링 검수</b>를 포함합니다. Markdown 구성안만 있는 상태를 최종 제출본 완료로 보고하지 않습니다.')
table(['검수 항목', '에이전트가 확인할 내용'], [
    ['디자인', '잘림·겹침·표 넘침·읽기 어려운 글자, 원본 패턴과 차이'],
    ['평가 대응', '필수 목차·평가기준 누락, 제안서 절·슬라이드·증빙 연결'],
    ['발표', '제한 시간, 실제 PPTX 장수와 계획 일치, 대본·Q&A 정합성'],
    ['사업 정보', '이전 고객명·가격·인력·실적 잔존, 수치와 일정 불일치'],
    ['제출', '지정 형식과 파일 실재, 최종 버전, 필수 첨부와 제출 조건'],
], [.21, .79])
sub('사용자 판단과 에이전트 검수')
body('<b>사용자:</b> 실제 예산·인력·실적·수행 범위를 확인하고 사업 결정을 전달합니다. 실제 담당자의 승인이나 기존 승인 문서를 근거로 사용할 수 있습니다.')
body('<b>에이전트:</b> 기능 점검, 환경 진단, 생성본 렌더링·비교 검수와 기록 관리를 수행합니다. 사용자에게 개발 테스트를 넘기거나 개인 템플릿 제출을 설치 조건으로 요구하지 않습니다.')
card('내용을 수정하고 다시 검수할 때', '이번 제안서의 인력 계획을 전달한 확정 내용으로 수정해줘.\n산정서·일정·발표자료·대본의 관련 내용을 함께 맞춰줘.\n변경된 파일을 다시 렌더링·검수하고 영향받는 승인과 버전을 갱신해줘.')
sub('제작 도구와 제출 권한')
body('호스트가 지정 형식 제작·렌더링을 지원하지 않으면 에이전트는 가능한 초안을 작성하고 미완료 형식과 원인을 명시합니다. HWP가 필수면 제출 조건을 먼저 확인하게 하세요. 외부 제출은 사용자가 부여한 대상·권한에 따라 수행합니다.')

# 10 - Award
chapter(9, '수주 후 프로젝트 관리로 전환', 'DELIVERY LIFECYCLE / 09')
body('수주 후에도 같은 프로젝트를 사용합니다. 최종 계약서·협상 결과·납품 조건을 전달해 제안 내용과 달라진 점을 반영합니다.')
card('수주 후 착수 요청', 'a-portal 사업을 수주했어. 최종 계약서와 협상 결과를 등록해줘.\n제안과 달라진 범위·일정·납품 조건을 비교해줘.\n확정 근거를 반영하고 착수 계획, 역할, 마일스톤,\n요구사항 기준선과 검수 기준을 준비해줘.\n필요한 검토·미결정을 정리하고 현재 단계에서 가능한 작업을 진행해줘.')
table(['단계', '주요 작업'], [
    ['01 사업 검토', '사업 목표·타당성·리스크와 제안 진행 검토'],
    ['02 제안', '정성제안서·산정·평가 대응·발표·제출 준비'],
    ['03 착수', '계약 범위·역할·일정·납품 및 검수 기준'],
    ['04 기획·설계', '요구사항, 화면 흐름, 데이터·API·구조 설계'],
    ['05 디자인', '디자인 기준과 화면·컴포넌트'],
    ['06 퍼블리싱', '화면 구현, 반응형·접근성'],
    ['07 구현', '기능 개발, 코드·요구사항·작업 연결'],
    ['08 검증', '테스트, 결함·회귀·수용 기준 확인'],
    ['09 배포', '배포·복구 계획, 릴리스와 실행 증거'],
    ['10 납품', '검수·인수·매뉴얼·산출물 목록'],
    ['11 유지보수', '장애·변경·운영 점검과 릴리스 반복'],
], [.28, .72])
body('현재 단계는 project.json과 실제 검토 근거로 관리합니다. 필수 검토와 완료 기준을 충족한 뒤 순서대로 이동합니다. 소규모 사업은 적용 제외 사유를 기록해 조정할 수 있습니다.')

# 11 - Daily
chapter(10, '개발·배포·유지보수 요청하기', 'DAILY OPERATIONS / 10')
body('프로젝트별로 GitHub 저장소, 기술 스택, 테스트 방법과 배포 대상을 알려주세요. 실제로 연결된 도구와 권한 안에서 작업하고, 버전·커밋·검증 결과를 기록합니다.')
card('설계·구현', '확정 요구사항으로 화면 흐름과 데이터·API 설계를 작성해줘.\n현재 승인된 우선순위 기능을 구현하고 관련 테스트를 실행해줘.\n요구사항·설계·코드·테스트 연결과 작업 기록을 갱신해줘.')
card('고객 변경 요청', '고객이 예약 기능에 알림 발송을 추가해 달라고 했어.\n첨부한 고객 답변을 등록하고 범위·공수·일정·설계 영향을 분석해줘.\n변경 기록과 검토할 결정을 정리하고 승인된 범위의 작업을 진행해줘.')
card('배포·납품 준비', '현재 검증 결과를 기준으로 배포 준비 상태를 점검해줘.\n배포·복구 절차, 릴리스 기록, 납품 산출물 목록과 운영 매뉴얼을 정리해줘.\n실제 실행 증거와 미완료 사항을 구분해서 알려줘.')
card('유지보수·장애 대응', '전달한 장애 내용을 등록하고 원인을 분석해줘.\n허용된 범위에서 수정과 회귀 테스트를 진행해줘.\n영향받는 요구사항·문서·릴리스와 인수인계 기록을 갱신해줘.')
body('승인된 범위나 산출물이 바뀌면 영향받는 검토를 다시 확인합니다. AI가 사람의 승인·배포 성공·검수 완료를 임의로 만들어 기록하지 않습니다. 운영 환경 변경은 부여된 대상·권한을 따릅니다.')

# 12 - Resume
chapter(11, '이어하기와 산출물 찾기', 'HANDOFF / 11')
body('다음 날 새 채팅을 시작하거나 AI를 바꿀 때 <b>같은 고객 프로젝트 폴더</b>를 엽니다. 예시 경로는 내 프로젝트/projects/a-portal/입니다. 두 제품에 플러그인이 설치되어 있어야 합니다.')
card('Codex ↔ Claude Code 인수인계 요청', 'Project Lifecycle Agent로 이 프로젝트를 이어서 진행해줘.\nproject.json, 지침, HANDOFF.md, 원장과 실제 산출물을 확인해줘.\n현재 단계, 최근 변경, 미완료 작업과 검토할 사항을 정리한 뒤\n현재 승인된 범위에서 다음 작업을 진행해줘.')
table(['파일·폴더', '보관 내용'], [
    ['project.json', '사업 유형·방법론·현재 단계·기술 정보'],
    ['AGENTS.md · CLAUDE.md\nprocess/', '프로젝트 지침과 단계별 규칙'],
    ['HANDOFF.md', '실제 변경·검증·미결정·다음 작업'],
    ['inputs/', 'RFP·고객 답변 등 원본, 출처와 버전'],
    ['templates/', '참고 원본·선택·분석·비교 검수'],
    ['docs/ · registers/', '업무 문서, 요구사항·작업·산출물·리스크 원장'],
    ['working/templates/\nexports/', '제작 시 만드는 작업 사본과 결과 파일'],
], [.34, .66])
body('사용자가 관리 파일을 직접 수정할 필요는 없습니다. 에이전트가 기록과 실제 파일을 대조해 재개합니다. 상위 폴더에 여러 프로젝트가 있으면 “a-portal을 이어서 진행해줘”처럼 대상을 지정하세요.')
body('공유되는 것은 파일과 관리 기록이며 대화 이력은 자동 동기화되지 않습니다. 다른 PC에서는 원본·산출물을 포함한 필요한 프로젝트 파일을 안전하게 동기화해야 합니다. 고객 자료를 공개 GitHub 저장소에 올리기 전 제외 처리를 요청하세요.')

# 13 - Update / FAQ
chapter(12, '업데이트와 문제 해결', 'SUPPORT / 12')
card('업데이트 요청', 'Project Lifecycle Agent의 설치 소스와 버전을 확인하고 최신 main으로 업데이트해줘.\n기존 고객 프로젝트와 원본 자료는 보존해줘.\n새 양식이 필요하면 차이·적용 범위·이행 방법을 먼저 정리해줘.')
body('두 제품은 각각 업데이트하고 새 세션에서 사용합니다. 베타·공개 main·설치본의 실제 버전을 구분합니다. 기존 프로젝트는 이행 미리보기와 관리정보 백업 후 적용합니다. 문서·ID·단계는 덮어쓰지 않으며, 이후 수정이 있으면 이행 되돌리기를 중단합니다.')
table(['상황', '에이전트에게 요청할 내용'], [
    ['스킬이 안 보임', '설치·활성화·로딩 버전을 진단. 새 세션에서 명시적 스킬 호출.'],
    ['옛 버전만 보임', 'marketplace의 태그 고정 여부와 main 전환 방법 확인.'],
    ['파일을 못 읽음', '실제 경로·파일 권한·형식·OCR 도구 확인, 못 읽은 부분 명시.'],
    ['스타일이 다름', '대표 파일과 해당 페이지·슬라이드, 원하는 특징을 지정해 재비교·수정.'],
    ['작업 중 종료', '현재 상태와 복구 필요를 진단하고 사용자 추가 변경을 보존하며 해결.'],
    ['단계 이동 불가', '미완료 산출물·검토·변경된 승인 근거를 확인하고 필요한 작업 수행.'],
], [.25, .75])
sub('자료와 실행 범위')
body('고객 프로젝트와 참고 원본은 플러그인과 분리합니다. 기존 프로젝트를 새 기본형으로 통째로 덮어쓰지 않습니다. 이 플러그인은 활성 세션에서 실행되며 상시 서버·자동 스케줄러를 포함하지 않습니다.')
body('원본 분석 규칙은 프로젝트 파일에 저장합니다. 모델을 학습시키는 기능은 없으며, 자료가 호스트 모델에 전달되는 방식은 사용하는 제품의 데이터 정책과 설정을 따릅니다. 오류 해결을 위해 고객 폴더나 캐시를 임의로 삭제하지 마세요.')

# 14 - Desk reference
chapter(13, '바로 쓰는 요청문과 참고 링크', 'DESK REFERENCE / 13')
table(['하고 싶은 일', '대화 요청 예시'], [
    ['새 사업', '이 RFP로 외주 사업을 시작하고 원본·요구사항·고객 질문을 정리해줘.'],
    ['자체 서비스', '이 아이디어로 자체 서비스를 시작하고 MVP 범위와 성공 지표를 작성해줘.'],
    ['템플릿 활용', '지정 폴더를 분석하고 A 문서와 B 발표의 스타일·작성 패턴을 적용해줘.'],
    ['제안·발표', '평가기준에 맞춰 제안서·발표·대본·Q&A·제출 점검을 제작하고 검수해줘.'],
    ['작업 재개', '현재 프로젝트 기록과 실제 파일을 확인하고 승인된 범위에서 이어서 진행해줘.'],
    ['상태 확인', '현재 단계, 완료 파일, 미완료 작업, 리스크와 다음 사업 결정을 알려줘.'],
], [.22, .78])
sub('공식 저장소와 상세 안내')
links = [
    ('GitHub 저장소 · 최신 README', REPO),
    ('설치·업데이트 상세 안내', REPO + '/blob/main/docs/INSTALL.md'),
    ('템플릿 등록·스타일 분석 안내', REPO + '/blob/main/docs/REFERENCE-TEMPLATES.md'),
    ('제안 패키지 상세 안내', REPO + '/blob/main/docs/PROPOSAL.md'),
    ('엔진 운영·기존 프로젝트 적용', REPO + '/blob/main/docs/ENGINE.md'),
    ('OpenAI 공식 플러그인 문서', 'https://developers.openai.com/plugins/build/plugins'),
    ('Claude Code 공식 설치 문서', 'https://code.claude.com/docs/en/discover-plugins'),
]
for label, url in links:
    story.append(p(f'<link href="{url}" color="#087F8C">{escape(label)}</link>', 'body'))
sub('이 매뉴얼의 기준')
body(f'v{VERSION} 베타 기준입니다. SI·제품·API·유지보수 프로필과 릴리스 범위, 작업 재개, 관리 변경 복원, 전체 출력 검수와 로컬 현황을 제공합니다. 실제 업무 결정·내용·디자인은 근거를 확인해야 합니다. 공개 배포와 설치본의 버전은 별도로 확인합니다.')
story.append(p(f'갱신일 {MANUAL_DATE}  |  안내 링크는 클릭할 수 있습니다.', 'small'))

def page_header(c, doc):
    c.setStrokeColor(LINE)
    c.line(50, 40, W-50, 40)
    c.setFont('Malgun', 8)
    c.setFillColor(MUTED)
    c.drawString(50, 27, f'PROJECT IEUM  |  v{VERSION}')
    c.drawRightString(W-50, 27, f'{doc.page:02d} / 14')
    if doc.page == 1:
        c.setFillColor(TEAL)
        c.rect(0, H-18, W, 18, fill=1, stroke=0)
        return
    c.setFillColor(TEAL)
    c.rect(50, H-43, 24, 3, fill=1, stroke=0)
    c.setFont('Malgun', 8)
    c.setFillColor(MUTED)
    c.drawRightString(W-50, H-43, '사용자 매뉴얼 / USER GUIDE')

doc=SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=50, leftMargin=50, topMargin=68, bottomMargin=60,
                      title='프로젝트이음 사용자 매뉴얼', author='Project Ieum',
                      subject='Codex·Claude Code 설치, 참고 템플릿, 제안 작성 및 프로젝트 관리')
doc.build(story, onFirstPage=page_header, onLaterPages=page_header)
reader=PdfReader(OUT)
text='\n'.join(page.extract_text() or '' for page in reader.pages)
for token in ['사용자 매뉴얼', 'codex plugin marketplace add', '/plugin install', 'qualitative/', '유지보수', 'HANDOFF.md']:
    assert token in text, f'Missing text: {token}'
assert len(reader.pages)==14, f'Expected 14 pages, got {len(reader.pages)}'
report={'version':VERSION, 'file':str(OUT), 'pages':len(reader.pages), 'bytes':OUT.stat().st_size,
        'page_text_lengths':[len(page.extract_text() or '') for page in reader.pages]}
(ROOT/'tmp/pdfs/validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
import shutil
destination = ROOT / 'website/public/downloads/project-ieum-user-guide.pdf'
shutil.copy2(OUT, destination)
import hashlib
report['sha256'] = hashlib.sha256(destination.read_bytes()).hexdigest()
(ROOT / 'website/public/downloads/manual-metadata.json').write_text(json.dumps({key: report[key] for key in ('version','pages','bytes','sha256')}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
