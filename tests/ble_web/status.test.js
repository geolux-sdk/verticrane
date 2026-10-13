/*
 * 끊긴 뒤의 Wi-Fi 정보 표시.
 * 장비와 끊겨 있으면 화면의 IP·SSID 는 '지금' 이 아니라 '마지막으로 본' 값이다.
 * 지우면 방금 받아 낸 IP 를 잃고, 그대로 두면 거짓말이 된다 — 흐리게 낮추고
 * 언제 기준인지 밝히는 것이 맞다.
 */
const { loadApp, makeLiveDevice } = require("./harness");

const live = makeLiveDevice("dev-live", "Pi-BLE-3044");
const app = loadApp({ devices: [live] });
const { $, tick, cards } = app;

module.exports = async function run(t) {
  await app.load();
  await tick();
  cards()[0].click();
  await tick(60);
  live.notify({ ev: "status", connected: true, ssid: "Geolux_RND", ip: "192.168.0.25",
                state: "connected", gateway: "192.168.0.1", mac: "aa", hostname: "pi-bt" });
  await tick();

  t.is($("ip").textContent, "192.168.0.25", "연결 중에는 받은 IP 를 그대로 보여준다");
  t.ok(!$("readout").classes.has("stale"), "연결 중에는 흐리게 하지 않는다");
  t.ok($("staleNote").hidden, "연결 중에는 안내문이 없다");

  live.drop();   // 갑작스런 끊김
  await tick(60);

  t.ok($("readout").classes.has("stale"), "끊기면 정보를 흐리게 낮춘다");
  t.ok(!$("staleNote").hidden, "끊기면 안내문을 띄운다");
  t.ok($("staleNote").textContent.includes("마지막 확인값"),
       "언제 기준 값인지 밝힌다");
  t.is($("ip").textContent, "192.168.0.25", "받아 둔 IP 를 지우지는 않는다");
  t.ok(!$("copyIp").disabled, "끊겨도 IP 복사는 계속 가능하다");
};
