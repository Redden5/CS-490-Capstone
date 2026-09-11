import requests
from bs4 import BeautifulSoup

res = requests.get('https://mubert.marshall.edu/scheduleofcourses.php?term=202701')
soup = BeautifulSoup(res.text, 'html.parser')
courses = soup.find_all('a')

#Undergrad Courses
head_tag = soup.find('h3')
#Find University Tag
university_tag = soup.head.title.text

print(university_tag)
print(head_tag.text)

#Prints all courses(undergrad + grad) with subject code and name
for link in soup.find_all('a', href=True):
    span = link.find('span', class_='subjFiller')

    if span:
        subject_code = span.get_text(strip=True)
        subject_name = link.get_text(" ", strip=True).replace(subject_code, "",1).strip()
        print(subject_code, "-", subject_name)

# Continue on separating under grad and graduate classes and translating data into tables via supabase
#to create future degree audit system
