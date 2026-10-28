"""Package only the public fictional example for the documentation website."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'examples' / 'customer-portal'
OUTPUT = ROOT / 'website' / 'public' / 'downloads' / 'customer-portal-example.zip'
NAMES = ('README.md', 'RFP.md', '01-requirements.md', '02-proposal.md',
         '03-estimate.md', '04-evaluation.md', '05-presentation.md',
         '06-submission.md', '07-handoff.md')


def package():
    contents = {}
    for name in NAMES:
        path = SOURCE / name
        if path.is_symlink() or not path.resolve().is_relative_to(SOURCE.resolve()):
            raise ValueError('Public examples must be regular files within the example folder.')
        contents[name] = path.read_bytes()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix('.zip.tmp')
    try:
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
            for name, content in contents.items():
                info = ZipInfo('customer-portal/' + name, date_time=(2026, 10, 28, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Public example ZIP verification failed.')
        temporary.replace(OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Public example: {len(contents)} files, {OUTPUT.name}')


if __name__ == '__main__':
    package()
