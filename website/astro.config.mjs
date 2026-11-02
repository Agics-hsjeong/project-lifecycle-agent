import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://agics-hsjeong.github.io',
  base: '/project-lifecycle-agent',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: '프로젝트이음',
      favicon: '/brand/symbol.png',
      description: '제안부터 운영까지, 프로젝트의 모든 단계를 잇다. Codex와 Claude Code를 위한 오픈소스 프로젝트 에이전트.',
      defaultLocale: 'root',
      locales: { root: { label: '한국어', lang: 'ko' } },
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/Agics-hsjeong/project-lifecycle-agent' }],
      editLink: { baseUrl: 'https://github.com/Agics-hsjeong/project-lifecycle-agent/edit/main/website/' },
      customCss: ['./src/styles/docs.css'],
      sidebar: [
        { label: '처음 시작하기', items: [
          { label: '전체 흐름', slug: 'docs/start' },
          { label: 'Codex 설치', slug: 'docs/install/codex' },
          { label: 'Claude Code 설치', slug: 'docs/install/claude' },
          { label: '첫 프로젝트 만들기', slug: 'docs/first-project' },
        ] },
        { label: '업무별 가이드', items: [
          { label: 'v2 사용과 업데이트', slug: 'docs/guides/v2' },
          { label: '자기 템플릿 활용', slug: 'docs/guides/templates' },
          { label: '제안서와 발표자료', slug: 'docs/guides/proposal' },
          { label: '수주부터 유지보수까지', slug: 'docs/guides/lifecycle' },
          { label: 'Codex ↔ Claude 이어하기', slug: 'docs/guides/handoff' },
        ] },
        { label: '예제와 참고', items: [
          { label: '고객 포털 예제', slug: 'docs/examples/portal' },
          { label: '파일과 산출물', slug: 'docs/reference/files' },
          { label: '업데이트·문제 해결', slug: 'docs/reference/troubleshooting' },
          { label: '기여·피드백', slug: 'docs/contributing' },
          { label: '변경 이력', slug: 'docs/changelog' },
        ] },
      ],
    }),
  ],
});
