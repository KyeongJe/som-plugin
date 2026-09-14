# Snowflake 자격증명

## 먼저 — 이 폴더의 예시에는 실제 값이 없다

`account` · `database` · `warehouse` 는 전부 placeholder 다. 이 저장소는 공개이고,
실제 account identifier 가 적혀 있으면 로그인 엔드포인트가 그대로 특정된다.
비밀은 아니지만(모든 사용자의 Snowflake URL 에 들어 있다), 공개해두면 공격이
"표적을 찾는 일" 에서 "자격증명 하나를 노리는 일" 로 바뀐다. 방어선은 이 문자열의
비밀성이 아니라 **MFA 와 network policy** 이므로, 그쪽이 켜져 있는지부터 확인한다.

본인 값은 여기서 확인한다:

| 항목 | 어디서 |
|---|---|
| `account` | Snowflake 웹 UI 좌하단 계정 메뉴 → Account → Account Identifier. 접속 URL `https://<account>.snowflakecomputing.com` 의 앞부분과 같다 |
| `user` | 로그인 ID (보통 회사 이메일) |
| `database` · `warehouse` | 팀에서 쓰는 값. 모르면 데이터 담당자에게 묻는다 |

## 대화형 (기본)

`sf_login_info.json.sample` 을 복사해 위 네 값을 본인 것으로 바꾼다.
`authenticator: externalbrowser` 라 **파일에 비밀이 없다**.

배치는 커넥션 하나로 도니까 SSO 프롬프트도 실행당 한 번이다.

```bash
cp sf_login_info.json.sample /path/to/project/sf_login_info.json
```

`.gitignore` 가 `sf_login_info.json` 을 이미 제외한다.

## 무인 실행 (RSA)

`private_key_file` 에 **경로만** 넣는다. 키 자체를 config 에 넣으면 거부된다.

```json
{
  "account": "<ORG>-<ACCOUNT>",
  "user": "<service-account>",
  "private_key_file": "C:/Users/<you>/.snowflake/keys/som.p8",
  "database": "<DATABASE>",
  "warehouse": "<WAREHOUSE>"
}
```

암호는 OS keyring 에 둔다. repo 나 env 파일에 두지 않는다.

```bash
python -c "import keyring; keyring.set_password('somsql','<ORG>-<ACCOUNT>','<passphrase>')"
```

## 하지 말 것

- **동기 폴더(OneDrive·SharePoint·Dropbox) 안에 키를 두지 않는다.** `somsql` 이
  거부한다. 동기 서비스가 이전 버전을 보관하므로 나중에 삭제해도 남는다.
- config 에 `password` · `private_key` · `token` 을 인라인으로 넣지 않는다. 거부된다.
- 키를 복사·이동·출력하지 않는다. 이 클라이언트는 키가 **어디 있는지** 만 읽는다.

키가 한 번이라도 동기 트리에 있었다면 **rotate 대상**이다.
`/som:doctor` 가 프로젝트 트리에서 `*.p8` · `*.pem` · `id_rsa` 를 찾으면 hard fail 한다.
