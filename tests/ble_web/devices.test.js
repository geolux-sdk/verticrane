/*
 * 장비 목록 흐름: 목록 → 선택 → 연결 → 끊기.
 * 여기서 잡는 것들(전부 실제로 겪은 버그다):
 *  - 페이지를 열면 예전 장비에 저절로 붙는가 (붙으면 안 된다)
 *  - 장비가 꺼져 있을 때 취소도 장비 교체도 못 하고 갇히는가
 *  - 연결에 실패한 뒤 '붙었다 끊기' 를 무한 반복하는가
 */
const { loadApp, makeLiveDevice, makeDeadDevice, makeFlakyDevice } = require("./harness");

const live = makeLiveDevice("dev-live", "Pi-BLE-3044");
const dead = makeDeadDevice("dev-dead", "Pi-BLE-7A21");
const flaky = makeFlakyDevice("dev-flaky", "Pi-BLE-91C0");

const app = loadApp({ devices: [live, dead, flaky] });
const { $, tick, cards, screen } = app;

module.exports = async function run(t) {
  await app.load();
  await tick();
  t.is(screen(), "장비목록", "로드하면 장비 목록이 먼저 뜬다");
  t.is(cards().length, 3, "권한 있는 장비가 모두 목록에 뜬다");
  t.ok(!live.gatt.connected && !dead.gatt.connected, "자동으로 연결하지 않는다");
  t.ok($("disconnect").hidden, "고른 장비가 없으면 끊기 버튼을 감춘다");

  // 꺼져 있는 장비 — 시도가 끝없이 이어지는 동안에도 빠져나갈 수 있어야 한다
  cards()[1].click();
  await tick(60);
  t.is($("disconnect").textContent, "중단", "시도 중에는 '중단' 으로 바뀐다");
  t.ok(!$("disconnect").disabled, "시도 중에도 중단을 누를 수 있다");

  $("disconnect").click();
  await tick(60);
  t.is(screen(), "장비목록", "중단하면 장비 목록으로 돌아온다");

  // 정상 장비
  cards()[0].click();
  await tick(60);
  live.notify({ ev: "hello", name: "Pi-BLE-3044", fw: "test", proto: 4 });
  live.notify({ ev: "status", connected: true, ssid: "Geolux_RND", ip: "192.168.0.25",
                state: "connected", gateway: "192.168.0.1", mac: "aa", hostname: "pi-bt" });
  await tick();
  t.ok(live.gatt.connected, "고른 장비에 연결된다");
  t.is(screen(), "설정", "연결되면 설정 화면으로 넘어간다");
  t.is($("ip").textContent, "192.168.0.25", "받은 IP 를 표시한다");

  $("disconnect").click();
  await tick(60);
  t.ok(!live.gatt.connected, "끊기를 누르면 연결이 끊긴다");
  t.is(screen(), "장비목록", "끊으면 장비 목록으로 돌아온다");
  t.ok(cards()[0].children[0].children[0].textContent.includes("192.168.0.25"),
       "목록 카드에 마지막 IP 가 남는다");

  // GATT 준비에서 실패하는 장비 — 재연결 루프에 빠지면 안 된다
  cards()[2].click();
  await tick(200);
  t.is(flaky.connects, 1, "실패한 연결을 자동으로 재시도하지 않는다");
  t.is(screen(), "장비목록", "실패하면 장비 목록으로 돌아온다");
  t.ok($("disconnect").disabled, "실패 뒤 '시도 중' 상태가 남지 않는다");
};
