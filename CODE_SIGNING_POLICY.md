# Code signing policy — CuePrecise

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

관련 서비스: [SignPath.io](https://signpath.io/) ·
[SignPath Foundation](https://signpath.org/)

저장소는 `https://github.com/Nattentia/cueprecise`이고 설치 파일 이름은
`cueprecise-setup.exe`다.

## 서명 대상과 절차

- 공식 Windows 실행 파일은 이 공개 저장소의 GitHub Actions에서만 빌드한다.
- 서명 요청은 버전이 표시된 GitHub Release 후보에 대해서만 만든다.
- 저장소 소유자가 빌드 출처와 검사 결과를 확인한 뒤 각 서명 요청을 승인한다.
- CuePrecise가 직접 만든 실행 파일과 최종 설치 프로그램만 CuePrecise의 서명 대상으로
  삼는다. 설치 프로그램에 포함되는 외부 오픈소스 구성 요소를 CuePrecise의 이름으로
  다시 서명하지 않는다.
- 서명된 파일은 GitHub Releases에서 배포하고 SHA-256 해시를 함께 게시한다.

## 역할

현재 프로젝트는 개인 유지보수 프로젝트이며 다음 역할은 GitHub 사용자
[`Nattentia`](https://github.com/Nattentia)가 담당한다.

- Committer/author: 소스와 빌드 설정을 변경한다.
- Reviewer: 외부 기여자의 변경사항과 배포 관련 변경사항을 검토한다.
- Approver: 릴리스 후보와 코드 서명 요청을 최종 승인한다.

프로젝트 참여자가 늘어나면 작성자와 승인자를 분리하고 이 문서에 담당자를 갱신한다.
프로젝트 관계자는 GitHub와 서명 서비스 계정에 2단계 인증을 사용해야 한다.

## 사용자 보호

- 기능, 네트워크 통신 및 데이터 보관은 [개인정보 및 네트워크 정책](PRIVACY.md)에
  공개한다.
- 설치 프로그램이 만드는 변경사항과 제거 방법을 사용자 문서에 명시한다.
- 보안 문제는 [보안 정책](SECURITY.md)에 따라 비공개로 신고받는다.
