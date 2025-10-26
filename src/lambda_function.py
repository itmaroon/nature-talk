# -*- coding: utf-8 -*-
import json
import os
import logging

# AWS SDK（Lambda ランタイムに同梱。requirements.txtへ追加不要）
import boto3
from botocore.exceptions import ClientError

# 外部ライブラリ
import anthropic
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

# ローカル実行時のみ .env を読む（本番Lambdaでは無視される）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ======================================================================
# 機密情報の取得：環境変数 → SSM → Secrets Manager の順で解決（結果は env にキャッシュ）
#   ANTHROPIC_API_KEY_PARAM : SSM のパラメータ名（例: /nature-talk/prod/anthropic_api_key）
#   ANTHROPIC_SECRET_ID     : Secrets Manager のシークレット名（例: nature-talk/anthropic）
# ======================================================================

def _get_api_key() -> str:
    # 1) 既に環境変数にあればそれを使う
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]

    # 2) SSM Parameter Store（SecureString 前提）
    param_name = os.environ.get("ANTHROPIC_API_KEY_PARAM")
    if param_name:
        try:
            ssm = boto3.client("ssm")
            resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
            val = resp["Parameter"]["Value"]
            os.environ["ANTHROPIC_API_KEY"] = val  # キャッシュ
            logger.info(f"[SSM] fetched parameter: {param_name}")
            return val
        except ClientError as e:
            logger.error(f"[SSM] get_parameter failed: {e}")

    # 3) Secrets Manager
    secret_id = os.environ.get("ANTHROPIC_SECRET_ID")
    if secret_id:
        try:
            sm = boto3.client("secretsmanager")
            sec = sm.get_secret_value(SecretId=secret_id)
            val = sec.get("SecretString") or sec["SecretBinary"].decode("utf-8")
            os.environ["ANTHROPIC_API_KEY"] = val  # キャッシュ
            logger.info(f"[SecretsManager] fetched secret: {secret_id}")
            return val
        except ClientError as e:
            logger.error(f"[SecretsManager] get_secret_value failed: {e}")

    raise ValueError("ANTHROPIC_API_KEY が設定されていません")

_client = None
def _get_claude_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = _get_api_key()
        _client = anthropic.Anthropic(api_key=api_key)
    return _client

# スマートホームデバイスのシミュレーション
devices = {
    "light": {"status": "off", "brightness": 0},
    "aircon": {"status": "off", "temperature": 25}
}

def call_claude(user_message, conversation_history=None):
    """Claudeに問い合わせる"""
    if conversation_history is None:
        conversation_history = []

    logger.info("=== Claude API 呼び出し開始 ===")
    logger.info(f"ユーザーメッセージ: {user_message}")

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
        client = _get_claude_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=messages
        )

        usage = getattr(response, "usage", None)
        if usage:
            logger.info(f"トークン使用量 - 入力: {usage.input_tokens}, 出力: {usage.output_tokens}")

        claude_response = response.content[0].text.strip()
        logger.info(f"Claude生応答: {claude_response}")

        # 余計なコードブロック表記の除去
        if claude_response.startswith("```json"):
            claude_response = claude_response[7:]
        if claude_response.startswith("```"):
            claude_response = claude_response[3:]
        if claude_response.endswith("```"):
            claude_response = claude_response[:-3]
        claude_response = claude_response.strip()

        parsed = json.loads(claude_response)
        logger.info(f"パース済み応答: {parsed}")
        return parsed

    except json.JSONDecodeError as e:
        logger.error(f"JSON パースエラー: {e}")
        logger.error(f"パース失敗した文字列: {claude_response}")
        return {"response": "すみません、うまく理解できませんでした。もう一度お願いします。", "actions": []}
    except Exception as e:
        logger.error(f"Claude API エラー: {e}", exc_info=True)
        return {"response": "申し訳ございません。エラーが発生しました。", "actions": []}

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

# =========================
# Alexa スキルハンドラー
# =========================

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)
    def handle(self, handler_input):
        logger.info("=== LaunchRequest ===")
        speech_text = "ネイチャートークアシスタントです。何かお手伝いしましょうか？"
        return handler_input.response_builder.speak(speech_text).ask(speech_text).response

class FreeTalkIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("FreeTalkIntent")(handler_input)
    def handle(self, handler_input):
        logger.info("=== FreeTalkIntent ===")
        request = handler_input.request_envelope.request
        user_input = None
        try:
            slots = request.intent.slots
            if 'UserInput' in slots and slots['UserInput'].value:
                user_input = slots['UserInput'].value
        except Exception as e:
            logger.warning(f"スロット取得エラー: {e}")

        result = call_claude(user_input or "ユーザーが何か話しかけました")
        speech_text = result["response"]
        if result.get("actions"):
            execute_actions(result["actions"])
        return handler_input.response_builder.speak(speech_text).ask("他に何かありますか？").response

class SimplePhraseHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("SimplePhrase")(handler_input)
    def handle(self, handler_input):
        logger.info("=== SimplePhrase ===")
        result = call_claude("ユーザーが状態や感情を表現しました（暑い、寒い、疲れた等）。適切に応答してください。")
        speech_text = result["response"]
        if result.get("actions"):
            execute_actions(result["actions"])
        return handler_input.response_builder.speak(speech_text).ask("他に何かありますか？").response

class DeviceControlHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("DeviceControl")(handler_input)
    def handle(self, handler_input):
        logger.info("=== DeviceControl ===")
        result = call_claude("ユーザーがデバイスの操作を要求しました（つけて、消して等）。文脈から判断して適切に対応してください。")
        speech_text = result["response"]
        if result.get("actions"):
            execute_actions(result["actions"])
        return handler_input.response_builder.speak(speech_text).ask("他に何かありますか？").response

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)
    def handle(self, handler_input):
        logger.info("=== FallbackIntent ===")
        result = call_claude("ユーザーが話しかけましたが、正確には聞き取れませんでした。自然に会話を続けてください。")
        speech_text = result["response"]
        if result.get("actions"):
            execute_actions(result["actions"])
        return handler_input.response_builder.speak(speech_text).ask("他に何かありますか？").response

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)
    def handle(self, handler_input):
        logger.info("=== HelpIntent ===")
        speech_text = ("ネイチャートークアシスタントです。"
                       "「照明を明るくして」や「エアコンをつけて」のようなデバイス操作や、"
                       "「今日は寒いね」のような自然な会話もできます。")
        return handler_input.response_builder.speak(speech_text).ask("何かお手伝いしましょうか？").response

class CancelOrStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))
    def handle(self, handler_input):
        logger.info("=== Cancel/Stop ===")
        return handler_input.response_builder.speak("またお話ししましょう").response

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)
    def handle(self, handler_input):
        logger.info("=== SessionEnded ===")
        req = handler_input.request_envelope.request
        try:
            logger.info(f"reason={getattr(req,'reason',None)} error={getattr(req,'error',None)}")
        except Exception:
            pass
        return handler_input.response_builder.response

# 事故防止：キャッチオール & 例外ハンドラ
class CatchAllRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return True  # 最後に登録すること
    def handle(self, handler_input):
        env = handler_input.request_envelope
        rtype = getattr(env.request, 'object_type', None)
        iname = getattr(getattr(env.request, 'intent', None), 'name', None)
        logger.warning(f"[CatchAll] Unmatched request: type={rtype} intent={iname}")
        return handler_input.response_builder.speak("すみません、そのリクエストにはまだ対応していません。").response

class AllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True
    def handle(self, handler_input, exception):
        logger.error(f"[Exception] {exception}", exc_info=True)
        return handler_input.response_builder.speak("エラーが発生しました。もう一度お願いします。").response

# =========================
# スキルビルダー
# =========================

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(FreeTalkIntentHandler())
sb.add_request_handler(SimplePhraseHandler())
sb.add_request_handler(DeviceControlHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

# 最後にキャッチオール & 例外
sb.add_request_handler(CatchAllRequestHandler())
sb.add_exception_handler(AllExceptionHandler())

def lambda_handler(event, context):
    logger.info("=" * 80)
    logger.info("★ Lambda 起動")
    logger.info(json.dumps(event)[:2000])  # 先頭だけログ
    logger.info("=" * 80)
    return sb.lambda_handler()(event, context)
