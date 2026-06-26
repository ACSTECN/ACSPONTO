import bcrypt

senha = '12345'
hashed = bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt())
print(hashed.decode())
