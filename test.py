import bigsuds

api = bigsuds.BIGIP(hostname='localhost', username='admin', password='admin', verify=False, port=10443)
result = api.System.SystemInfo.get_uptime()
print(result)

result = api.System.SystemInfo.get_product_information()
print(result)
