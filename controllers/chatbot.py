from odoo import http
from odoo.http import request
import json
from odoo.tools import get_lang, is_html_empty, plaintext2html
from odoo.addons.im_livechat.controllers.main import LivechatController

class CustomChatbotController(http.Controller):

    @http.route('/custom_chatbot/get_channel_uuid', methods=["POST"], type="json", auth='public')
    def get_channel_uuid(self):
        try:
            # Obtener los datos del cuerpo JSON
            params = json.loads(request.httprequest.data.decode('utf-8'))
            channel_id = params.get('channel_id')
            anonymous_name = params.get('anonymous_name')
            previous_operator_id = params.get('previous_operator_id', None)
            chatbot_script_id = params.get('chatbot_script_id', None)
            persisted = params.get('persisted', True)

            # Validar parámetros obligatorios
            if not channel_id or not anonymous_name:
                return {"error": "Faltan los parámetros obligatorios: 'channel_id' o 'anonymous_name'."}

            user_id = None
            country_id = None

            # Si el usuario está autenticado
            if request.session.uid:
                user_id = request.env.user.id
                country_id = request.env.user.country_id.id
            else:
                # Si geoip está habilitado, agregar información del país
                if request.geoip.country_code:
                    country = request.env['res.country'].sudo().search([('code', '=', request.geoip.country_code)], limit=1)
                    if country:
                        country_id = country.id

            # Configurar el script del chatbot
            chatbot_script = False
            if chatbot_script_id:
                frontend_lang = request.httprequest.cookies.get('frontend_lang', request.env.user.lang or 'en_US')
                chatbot_script = request.env['chatbot.script'].sudo().with_context(lang=frontend_lang).browse(chatbot_script_id)

            # Obtener los valores del canal
            channel_vals = request.env["im_livechat.channel"].with_context(lang=False).sudo().browse(channel_id)._get_livechat_discuss_channel_vals(
                anonymous_name,
                previous_operator_id=previous_operator_id,
                chatbot_script=chatbot_script,
                user_id=user_id,
                country_id=country_id,
                lang=request.httprequest.cookies.get('frontend_lang')
            )

            if not channel_vals:
                return {"error": "No se encontraron valores para el canal proporcionado."}

            # Crear el canal persistente
            channel = request.env['discuss.channel'].with_context(mail_create_nosubscribe=False).sudo().create(channel_vals)

            # Devolver únicamente el UUID del canal creado
            return {"channel_uuid": channel.uuid}

        except Exception as e:
            return {"error": f"Error procesando la solicitud: {str(e)}"}

    @http.route('/custom_chatbot/restart', type="json", auth="public", cors="*")
    def custom_chatbot_restart(self):
        # Obtiene los parámetros desde el cuerpo JSON
        params = json.loads(request.httprequest.data.decode('utf-8'))
        channel_uuid = params.get('channel_uuid')
        chatbot_script_id = params.get('chatbot_script_id')

        # Validar los parámetros
        if not channel_uuid or not chatbot_script_id:
            return {"error": "Faltan los parámetros 'channel_uuid' o 'chatbot_script_id'."}

        # Reutiliza la lógica existente
        discuss_channel = request.env['discuss.channel'].sudo().search([('uuid', '=', channel_uuid)], limit=1)
        chatbot = request.env['chatbot.script'].sudo().browse(chatbot_script_id)
        if not discuss_channel or not chatbot.exists():
            return {"error": "Canal o script no encontrado."}

        chatbot_language = self._get_chatbot_language()
        return discuss_channel.with_context(lang=chatbot_language)._chatbot_restart(chatbot).message_format()[0]

    @http.route('/custom_chatbot/post_welcome_steps', type="json", auth="public", cors="*")
    def custom_chatbot_post_welcome_steps(self):
        try:
            # Intentar obtener los datos JSON de la solicitud
            params = json.loads(request.httprequest.data.decode('utf-8'))
            channel_uuid = params.get('channel_uuid')
            chatbot_script_id = params.get('chatbot_script_id')

            # Validar los parámetros
            if not channel_uuid or not chatbot_script_id:
                return {"error": "Faltan los parámetros 'channel_uuid' o 'chatbot_script_id'."}

            # Lógica existente del chatbot
            chatbot_language = self._get_chatbot_language()
            discuss_channel = request.env['discuss.channel'].sudo().search(
                [('uuid', '=', channel_uuid)], limit=1
            ).with_context(lang=chatbot_language)

            chatbot = request.env['chatbot.script'].sudo().browse(chatbot_script_id).with_context(lang=chatbot_language)
            if not discuss_channel or not chatbot.exists():
                return {"error": "Canal o script no encontrado."}

            return chatbot._post_welcome_steps(discuss_channel).message_format()
        except Exception as e:
            return {"error": str(e)}

    @http.route('/custom_chatbot/step/trigger', type="json", auth="public", cors="*")
    def custom_chatbot_trigger_step(self):
        params = json.loads(request.httprequest.data.decode('utf-8'))
        channel_uuid = params.get('channel_uuid')
        chatbot_script_id = params.get('chatbot_script_id')

        if not channel_uuid:
            return {"error": "Falta el parámetro 'channel_uuid'."}

        chatbot_language = self._get_chatbot_language()
        discuss_channel = request.env['discuss.channel'].sudo().search([('uuid', '=', channel_uuid)], limit=1)
        if not discuss_channel:
            return {"error": "Canal no encontrado."}

        next_step = False
        if discuss_channel.chatbot_current_step_id:
            chatbot = discuss_channel.chatbot_current_step_id.chatbot_script_id
            user_messages = discuss_channel.message_ids.filtered(
                lambda message: message.author_id != chatbot.operator_partner_id
            )
            user_answer = request.env['mail.message'].sudo()
            if user_messages:
                user_answer = user_messages.sorted(lambda message: message.id)[-1]
            next_step = discuss_channel.chatbot_current_step_id._process_answer(discuss_channel, user_answer.body)
        elif chatbot_script_id:
            chatbot = request.env['chatbot.script'].sudo().browse(chatbot_script_id)
            if chatbot.exists():
                next_step = chatbot.script_step_ids[:1]

        if not next_step:
            return {"error": "No hay siguiente paso disponible."}

        posted_message = next_step._process_step(discuss_channel)
        return {
            'chatbot_posted_message': posted_message.message_format()[0] if posted_message else None,
            'chatbot_step': {
                'id': next_step.id,
                'answers': [{
                    'id': answer.id,
                    'label': answer.name,
                    'redirectLink': answer.redirect_link,
                } for answer in next_step.answer_ids],
                'isLast': next_step._is_last_step(discuss_channel),
                'message': plaintext2html(next_step.message) if not is_html_empty(next_step.message) else False,
                'type': next_step.step_type,
            }
        }

    def _get_chatbot_language(self):
        return request.httprequest.cookies.get('frontend_lang', request.env.user.lang or get_lang(request.env).code)

    @http.route('/custom_chatbot/answer/save', type="json", auth="public", cors="*")
    def chatbot_save_answer(self):
        # Leer los parámetros desde el cuerpo de la solicitud
        params = json.loads(request.httprequest.data.decode('utf-8'))
        channel_uuid = params.get('channel_uuid')
        message_id = params.get('message_id')
        selected_answer_id = params.get('selected_answer_id')

        # Logs de depuración
        print(f"Channel UUID: {channel_uuid}")
        print(f"Message ID: {message_id}")
        print(f"Selected Answer ID: {selected_answer_id}")

        # Buscar el canal de discusión
        discuss_channel = request.env['discuss.channel'].sudo().search([('uuid', '=', channel_uuid)], limit=1)
        if not discuss_channel:
            return {"error": "Canal no encontrado."}

        print(f"Discuss Channel ID: {discuss_channel.id}")

        # Buscar el mensaje del chatbot
        chatbot_message = request.env['chatbot.message'].sudo().search([
            ('mail_message_id', '=', message_id),
            ('discuss_channel_id', '=', discuss_channel.id),
        ], limit=1)

        if not chatbot_message:
            return {"error": "Mensaje del chatbot no encontrado."}

        print(f"Chatbot Message: {chatbot_message}")

        # Buscar la respuesta seleccionada
        selected_answer = request.env['chatbot.script.answer'].sudo().browse(selected_answer_id)

        if not selected_answer.exists():
            # Si no se encuentra la respuesta seleccionada, volver a enviar la pregunta original
            question_body = chatbot_message.script_step_id.message or "No se encontró la pregunta original."
            answers = chatbot_message.script_step_id.answer_ids
            answers_list = [{"id": answer.id, "label": answer.name} for answer in answers]

            return {
                "error": "Respuesta no encontrada o no válida.",
                "question": question_body,
                "answers": answers_list
            }

        print(f"Selected Answer: {selected_answer.name}")

        # Validar si la respuesta pertenece a las posibles respuestas del paso actual
        if selected_answer in chatbot_message.script_step_id.answer_ids:
            # Guardar la respuesta seleccionada
            chatbot_message.write({'user_script_answer_id': selected_answer_id})
            print(f"Respuesta seleccionada guardada: {selected_answer_id}")

            return {"message": "Respuesta guardada correctamente."}
        else:
            # Si la respuesta no es válida, volver a enviar la pregunta original
            question_body = chatbot_message.script_step_id.message or "No se encontró la pregunta original."
            answers = chatbot_message.script_step_id.answer_ids
            answers_list = [{"id": answer.id, "label": answer.name} for answer in answers]

            return {
                "error": "Respuesta no válida.",
                "question": question_body,
                "answers": answers_list
            }
