import json
import os
import anthropic
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# .envファイルを読み込む（ローカル実行時のみ）
try:
    from dotenv import load_dotenv
    load_dotenv()
except (ImportError, ModuleNotFoundError):
    pass

# Claude APIクライアント初期化
api_key = os.environ.get("ANTHROPIC_API_KEY")

if not api_key:
    raise ValueError("ANTHROPIC_API_KEY が設定されていません")

client = anthropic.Anthropic(api_key=api_key)

# スマートホームデバイスのシミュレーション
devices = {
    "light": {"status": "off", "brightness": 0},
    "aircon": {"status": "off", "temperature": 25}
}

def call_claude(user_message, conversation_history=[]):
    """Claudeに問い合わせる"""
    
    print(f"=== Claude API 呼び出し開始 ===")
    print(f"ユーザーメッセージ: {user_message}")
    
    system_prompt = """あなたはネイチャートークアシスタント、親しみやすいAIアシスタントです。

スマートホームデバイスの制御も可能ですが、それ以外の会話も自然に対応してください。

利用可能なデバイス:
- light（照明）: on/off, brightness 0-100
- aircon（エアコン）: on/off, temperature 16-30

ユーザーの発言を理解し、以下のJSON形式で応答してください：
{
  "response": "ユーザーへの自然な返答",
  "actions": [
    {"device": "light", "command": "on", "value": 80}
  ]
}

デバイス制御が不要な場合（雑談、質問など）、actionsは空配列にしてください。
日本語で自然に、親しみやすく会話してください。

重要: 応答は純粋なJSONのみで、```json```のようなマークダウンは使わないでください。"""

    messages = conversation_history + [
        {"role": "user", "content": user_message}
    ]
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=messages
        )
        
        print(f"トークン使用量 - 入力: {response.usage.input_tokens}, 出力: {response.usage.output_tokens}")
        
        claude_response = response.content[0].text
        print(f"Claude生応答: {claude_response}")
        
        # コードブロックを除去
        claude_response = claude_response.strip()
        if claude_response.startswith("```json"):
            claude_response = claude_response[7:]
        if claude_response.startswith("```"):
            claude_response = claude_response[3:]
        if claude_response.endswith("```"):
            claude_response = claude_response[:-3]
        claude_response = claude_response.strip()
        
        print(f"クリーンアップ後: {claude_response}")
        
        parsed = json.loads(claude_response)
        print(f"パース済み応答: {parsed}")
        
        return parsed
        
    except json.JSONDecodeError as e:
        print(f"JSON パースエラー: {e}")
        print(f"パース失敗した文字列: {claude_response}")
        return {
            "response": "すみません、うまく理解できませんでした。もう一度お願いします。",
            "actions": []
        }
    except Exception as e:
        print(f"Claude API エラー: {e}")
        import traceback
        traceback.print_exc()
        return {
            "response": "申し訳ございません。エラーが発生しました。",
            "actions": []
        }

def execute_actions(actions):
    """デバイス制御を実行（現在はシミュレーション）"""
    results = []
    
    for action in actions:
        device = action.get("device")
        command = action.get("command")
        value = action.get("value")
        
        if device in devices:
            if command == "on":
                devices[device]["status"] = "on"
            elif command == "off":
                devices[device]["status"] = "off"
            
            if value is not None:
                if device == "light":
                    devices[device]["brightness"] = value
                elif device == "aircon":
                    devices[device]["temperature"] = value
            
            results.append(f"{device}を制御しました")
    
    return results

# ========================================
# Alexaスキルハンドラー
# ========================================

class LaunchRequestHandler(AbstractRequestHandler):
    """スキル起動時"""
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)
    
    def handle(self, handler_input):
        print("=== LaunchRequest ハンドラー実行 ===")
        speech_text = "ネイチャートークアシスタントです。何かお手伝いしましょうか？"
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask(speech_text)
                .response
        )

class FreeTalkIntentHandler(AbstractRequestHandler):
    """自由な会話を処理（{UserInput}を含む発話）"""
    def can_handle(self, handler_input):
        return is_intent_name("FreeTalkIntent")(handler_input)
    
    def handle(self, handler_input):
        print("=== FreeTalkIntent ハンドラー実行 ===")
        
        request = handler_input.request_envelope.request
        user_input = None
        
        try:
            slots = request.intent.slots
            print(f"受け取ったスロット: {slots}")
            
            if 'UserInput' in slots and slots['UserInput'].value:
                user_input = slots['UserInput'].value
                print(f"✓ ユーザー入力: {user_input}")
            else:
                print("✗ スロットが空です")
        except Exception as e:
            print(f"スロット取得エラー: {e}")
        
        # Claudeに問い合わせ
        if user_input:
            result = call_claude(user_input)
        else:
            result = call_claude("ユーザーが何か話しかけました")
        
        speech_text = result["response"]
        
        if result.get("actions"):
            print(f"アクション実行: {result['actions']}")
            execute_actions(result["actions"])
        
        print(f"Alexaの応答: {speech_text}")
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask("他に何かありますか？")
                .response
        )

class SimplePhraseHandler(AbstractRequestHandler):
    """シンプルなフレーズ（「寒い」「暑い」など）"""
    def can_handle(self, handler_input):
        return is_intent_name("SimplePhrase")(handler_input)
    
    def handle(self, handler_input):
        print("=== SimplePhrase ハンドラー実行 ===")
        
        # サンプル発話に一致したが、具体的な内容は直接取れない
        # Claudeに一般的なプロンプトを送る
        result = call_claude("ユーザーが状態や感情を表現しました（暑い、寒い、疲れた等）。適切に応答してください。")
        
        speech_text = result["response"]
        
        if result.get("actions"):
            print(f"アクション実行: {result['actions']}")
            execute_actions(result["actions"])
        
        print(f"Alexaの応答: {speech_text}")
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask("他に何かありますか？")
                .response
        )

class DeviceControlHandler(AbstractRequestHandler):
    """デバイス制御コマンド（「つけて」「消して」など）"""
    def can_handle(self, handler_input):
        return is_intent_name("DeviceControl")(handler_input)
    
    def handle(self, handler_input):
        print("=== DeviceControl ハンドラー実行 ===")
        
        result = call_claude("ユーザーがデバイスの操作を要求しました（つけて、消して等）。文脈から判断して適切に対応してください。")
        
        speech_text = result["response"]
        
        if result.get("actions"):
            print(f"アクション実行: {result['actions']}")
            execute_actions(result["actions"])
        
        print(f"Alexaの応答: {speech_text}")
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask("他に何かありますか？")
                .response
        )

class FallbackIntentHandler(AbstractRequestHandler):
    """認識できなかった発話も処理"""
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)
    
    def handle(self, handler_input):
        print("=== FallbackIntent ハンドラー実行 ===")
        print("  Alexaが認識できなかった発話をClaudeに問い合わせます")
        
        result = call_claude("ユーザーが話しかけましたが、正確には聞き取れませんでした。自然に会話を続けてください。")
        
        speech_text = result["response"]
        
        if result.get("actions"):
            print(f"アクション実行: {result['actions']}")
            execute_actions(result["actions"])
        
        print(f"Alexaの応答: {speech_text}")
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask("他に何かありますか？")
                .response
        )

class HelpIntentHandler(AbstractRequestHandler):
    """ヘルプ"""
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)
    
    def handle(self, handler_input):
        print("=== HelpIntent ハンドラー実行 ===")
        speech_text = ("ネイチャートークアシスタントです。"
                      "「照明を明るくして」や「エアコンをつけて」のようなデバイス操作や、"
                      "「今日は寒いね」のような自然な会話もできます。"
                      "気軽に話しかけてください。")
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .ask("何かお手伝いしましょうか？")
                .response
        )

class CancelOrStopIntentHandler(AbstractRequestHandler):
    """終了時"""
    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))
    
    def handle(self, handler_input):
        print("=== CancelOrStopIntent ハンドラー実行 ===")
        speech_text = "またお話ししましょう"
        
        return (
            handler_input.response_builder
                .speak(speech_text)
                .response
        )

class SessionEndedRequestHandler(AbstractRequestHandler):
    """セッション終了時"""
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)
    
    def handle(self, handler_input):
        print("=== SessionEndedRequest ハンドラー実行 ===")
        
        request = handler_input.request_envelope.request
        
        if hasattr(request, 'reason'):
            print(f"セッション終了理由: {request.reason}")
        
        if hasattr(request, 'error'):
            print(f"エラー: {request.error}")
        
        # SessionEndedRequestには応答を返さない
        return handler_input.response_builder.response

# ========================================
# スキルビルダー
# ========================================

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(FreeTalkIntentHandler())
sb.add_request_handler(SimplePhraseHandler())
sb.add_request_handler(DeviceControlHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

# Lambda ハンドラー
def lambda_handler(event, context):
    print("=" * 80)
    print("★ Lambda 起動")
    print("=" * 80)
    
    try:
        return sb.lambda_handler()(event, context)
    except Exception as e:
        print(f"!!! エラー発生: {e}")
        import traceback
        traceback.print_exc()
        raise
