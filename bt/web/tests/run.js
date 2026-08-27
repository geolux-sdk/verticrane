/*
 * 웹앱 테스트 실행기:  node web/tests/run.js
 *
 * 각 테스트는 별도 프로세스에서 돌린다 — index.html 의 스크립트가 전역
 * (setInterval·document 등)에 붙어 살아 있어서, 한 프로세스에서 두 번 실행하면
 * 서로 간섭한다.
 */
const path = require("path");
const { fork } = require("child_process");

const SUITES = ["devices.test.js", "status.test.js"];

// 자식 프로세스로 불렸을 때: 지정된 스위트 하나를 실행한다.
if (process.env.SUITE) {
  const results = [];
  const t = {
    ok: (pass, name) => results.push([!!pass, name]),
    is: (actual, expected, name) =>
      results.push([actual === expected, name
        + (actual === expected ? "" : `  (기대: ${expected} / 실제: ${actual})`)]),
  };
  require(path.join(__dirname, process.env.SUITE))(t)
    .then(() => {
      let failed = 0;
      for (const [pass, name] of results) {
        if (!pass) failed++;
        console.log(`  ${pass ? "PASS" : "FAIL"}  ${name}`);
      }
      process.exit(failed ? 1 : 0);
    })
    .catch((e) => { console.log(`  ERROR ${e.stack}`); process.exit(1); });
} else {
  (async () => {
    let failed = 0;
    for (const suite of SUITES) {
      console.log(`\n${suite}`);
      const code = await new Promise((resolve) => {
        fork(__filename, [], { env: { ...process.env, SUITE: suite } })
          .on("exit", resolve);
      });
      if (code !== 0) failed++;
    }
    console.log(failed ? `\n실패한 스위트 ${failed}개` : "\n전부 통과");
    process.exit(failed ? 1 : 0);
  })();
}
