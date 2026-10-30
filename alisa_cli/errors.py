"""Public diagnostics shared with the updater; never include remote output."""

MESSAGES = {
    'ssh_unreachable': 'Нет подключения SSH к колонке. Проверьте её питание, сеть, IP и порт в настройках подключения.',
    'ssh_auth': 'SSH-доступ отклонён. Проверьте пользователя и сохранённый ключ в настройках подключения.',
    'ssh_hostkey': 'SSH-ключ колонки изменился или не прошёл проверку. Проверьте отпечаток в настройках подключения.',
    'ssh_timeout': 'Колонка не ответила по SSH вовремя. Проверьте подключение и повторите операцию.',
    'station_reply_invalid': 'Не удалось определить состояние клиента колонки. Проверьте устройство перед обновлением.',
}


class PublicError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])
