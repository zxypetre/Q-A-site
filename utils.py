from datetime import datetime, timedelta

#utils for templates

#TODO XXX: a rough way to convert time to china localtime
def utc_to_utc8(dt):
    if isinstance(dt, datetime):
        dt = dt + timedelta(hours=8)
        return dt.strftime(' %Y/%m/%d  %H:%M')
    else:
        return ''

if __name__ == '__main__':
    print(utc_to_utc8(1))
