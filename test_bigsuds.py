#!/usr/bin/env python
# -*- coding: utf-8 -*-

import bigsuds

def test_bigsuds_product_info():
    api = bigsuds.BIGIP(hostname='localhost', username='admin', password='admin', verify=False, port=10443)
    result = api.System.SystemInfo.get_uptime()
    assert int(result) > 1

    result = api.System.SystemInfo.get_product_information()
    assert 'package_edition' in result

def test_bigsuds_encoding():
    api = bigsuds.BIGIP(hostname='localhost', username='admin', password='admin', verify=False, port=10443)
    api.LocalLB.VirtualServer.create(
        definitions=[
            {
                'name': ['vip_c_1151llc33_https'],
                'address': ['1.1.1.1'],
                'port': 8080,
                'protocol': 'PROTOCOL_TCP'
            }
        ],
        wildmasks=['255.255.255.255'],
        resources=[
            {
                'type': 'RESOURCE_TYPE_REJECT',
            }
        ],
        profiles=[
            [
                {
                    'profile_context': 'PROFILE_CONTEXT_TYPE_ALL',
                    'profile_name': 'tcp'
                }
            ]
        ]
    )
    api.LocalLB.VirtualServer.set_description(
        virtual_servers=['vip_c_1151llc33_https'],
        descriptions=['11.11° WWC'.decode('utf-8')]
    )
    api.LocalLB.VirtualServer.get_description(['vip_c_1151llc33_https'])
    api.LocalLB.VirtualServer.delete_virtual_server(
        virtual_servers=['vip_c_1151llc33_https']
    )