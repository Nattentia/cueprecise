"""transcript.json + captions.json -> merged.json (CONTRACT.md 2절 준수).

owner: claude
status: 미구현

Gemini 전사를 골격으로 두고, 조사만 남고 명사가 사라진 공백 구간에
YouTube 자막의 영어 용어를 시각 기준으로 끌어와 채운다.

계획:
  1. transcript.json words 에서 소실 후보 구간을 찾는다.
     - 앞 단어 end 와 다음 단어 start 간격이 1.5초 초과
     - 공백 직후 단어가 조사로 시작 (이라는, 라는, 의, 와, 과, 를, 을 ...)
  2. 해당 시각 범위를 captions.json 에서 조회한다.
  3. 자막 쪽에만 있는 라틴 토큰을 추출한다.
  4. words 에 origin="youtube" 로 삽입한다. 시각은 자막 cue 것을 쓴다.
  5. 삽입으로 순서가 깨지지 않게 start 기준 재정렬.

주의: 자막 표기 오류가 그대로 들어온다 (retrievered, RG, EMR 등).
      정규화는 별도 단계로 분리한다. 이 파일에서 하지 않는다.
"""
raise NotImplementedError("merge.py 미구현")
