"""
    Created on Monday Feb 2 2022

    @author: Nishant Jain

    :copyright: (c) 2022 by Angel One Limited
"""

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

AUTH_TOKEN = 'Bearer eyJhbGciOiJIUzUxMiJ9.eyJ1c2VybmFtZSI6Ik4zMDMxMTIiLCJyb2xlcyI6MCwidXNlcnR5cGUiOiJVU0VSIiwiaWF0I' \
             'joxNjQzNjQ0OTE0LCJleHAiOjE3MzAwNDQ5MTR9.2bWdZzsHrM5xN3S647XyYaAmT520wy3eXyt5EbVRuCIgrI_92u4r2J6CXYYu89' \
             'l_76sAig3D2PUIeHmL6zYS-w'
API_KEY = 'qmu90MJS'
CLIENT_CODE = 'N303112'
FEED_TOKEN = '099775688'

correlation_id = "nishant_123_qwerty"
action = 1
mode = 3
# token_list = [{"exchangeType": 1, "tokens": ["10626", "5290"]},
#               {"exchangeType": 5, "tokens": ["234230", "234235", "234219"]}]
#
# token_list2 = [{"exchangeType": 5, "tokens": ["114", "115"]}]

token_list = [{"exchangeType": 3, "tokens": ["500209"]}]

sws = SmartWebSocketV2(AUTH_TOKEN, API_KEY, CLIENT_CODE, FEED_TOKEN)


def on_data(wsapp, message):
    print("Ticks: {}".format(message))


def on_open(wsapp):
    print("on open")
    sws.subscribe(correlation_id, mode, token_list)
    # sws.subscribe(correlation_id, mode, token_list2)


def on_error(wsapp, error):
    print(error)


def on_close(wsapp):
    print("Close")


# Assign the callbacks.
sws.on_open = on_open
sws.on_data = on_data
sws.on_error = on_error
sws.on_close = on_close

sws.connect()
