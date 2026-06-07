import random

def check_ability(score, difficulty=0):
    """Tirada de característica: 1d20 <= score + difficulty"""
    roll = random.randint(1, 20)
    success = roll <= (score + difficulty)
    return roll, success

def attack_roll(thac0, ac):
    """Tirada de ataque: 1d20 >= thac0 - ac"""
    roll = random.randint(1, 20)
    hit = roll >= (thac0 - ac)
    return roll, hit

def damage_roll(dice, sides):
    """Ejemplo: damage_roll(1,8) para 1d8"""
    return sum(random.randint(1, sides) for _ in range(dice))