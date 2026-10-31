"""Synthetic vocabulary for the data generator: names (with Devanagari forms where useful),
states, cities and nationalities. Everything here is generic; combinations are random and do
not describe real people. Phone numbers use a "9999" prefix and emails the reserved
`.example` domain (RFC 2606) so that no generated contact can belong to a real person.
"""

from __future__ import annotations

# (latin, devanagari or None)
Name = tuple[str, str | None]


def _pairs(text: str) -> list[Name]:
    """Parse 'Rahul:राहुल, Amit:अमित, Joseph' into [(latin, deva|None), ...]."""
    out: list[Name] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        latin, _, deva = item.partition(":")
        out.append((latin.strip(), deva.strip() or None))
    return out


# region -> {"M": first names, "F": first names, "S": surnames}
NAMES: dict[str, dict[str, list[Name]]] = {
    "NORTH": {
        "M": _pairs(
            "Rahul:राहुल, Amit:अमित, Rohit:रोहित, Ankit:अंकित, Vikas:विकास, Saurabh:सौरभ, "
            "Abhishek:अभिषेक, Aditya:आदित्य, Arjun:अर्जुन, Aarav:आरव, Vivek:विवेक, Deepak:दीपक, "
            "Sanjay:संजय, Manish:मनीष, Gaurav:गौरव, Nitin:नितिन, Pankaj:पंकज, Rajesh:राजेश, "
            "Sachin:सचिन, Varun:वरुण, Harsh:हर्ष, Kunal:कुणाल, Akash:आकाश, Shubham:शुभम, "
            "Prakash:प्रकाश, Ajay:अजय, Vijay:विजय, Mohit:मोहित, Yash:यश, Ravi:रवि"
        ),
        "F": _pairs(
            "Priya:प्रिया, Pooja:पूजा, Neha:नेहा, Anjali:अंजलि, Shivani:शिवानी, Kavita:कविता, "
            "Sunita:सुनीता, Ritu:रितु, Swati:स्वाति, Jyoti:ज्योति, Ananya:अनन्या, Aditi:अदिति, "
            "Riya:रिया, Sakshi:साक्षी, Nisha:निशा, Deepika:दीपिका, Megha:मेघा, Shreya:श्रेया, "
            "Komal:कोमल, Priyanka:प्रियंका, Divya:दिव्या, Kiran:किरण, Muskan:मुस्कान, "
            "Tanvi:तन्वी, Isha:ईशा"
        ),
        "S": _pairs(
            "Sharma:शर्मा, Verma:वर्मा, Gupta:गुप्ता, Singh:सिंह, Yadav:यादव, Mishra:मिश्रा, "
            "Pandey:पांडेय, Tiwari:तिवारी, Srivastava:श्रीवास्तव, Agarwal:अग्रवाल, Jain:जैन, "
            "Chauhan:चौहान, Saxena:सक्सेना, Dubey:दुबे, Tripathi:त्रिपाठी, Kumar:कुमार, "
            "Chaudhary:चौधरी, Rathore:राठौर, Joshi:जोशी, Bansal:बंसल, Goyal:गोयल, "
            "Rajput:राजपूत, Shukla:शुक्ला, Upadhyay:उपाध्याय, Meena:मीणा, Thakur:ठाकुर"
        ),
    },
    "WEST": {
        "M": _pairs(
            "Aniket:अनिकेत, Omkar:ओंकार, Siddharth:सिद्धार्थ, Tejas:तेजस, Pranav:प्रणव, "
            "Sagar:सागर, Mahesh:महेश, Ganesh:गणेश, Swapnil:स्वप्निल, Rohan:रोहन, "
            "Atharva:अथर्व, Shreyas:श्रेयस, Vaibhav:वैभव, Akshay:अक्षय, Nikhil:निखिल"
        ),
        "F": _pairs(
            "Sneha:स्नेहा, Prajakta:प्राजक्ता, Rutuja:ऋतुजा, Shruti:श्रुती, Gauri:गौरी, "
            "Aishwarya:ऐश्वर्या, Madhuri:माधुरी, Pallavi:पल्लवी, Sayali:सायली, "
            "Vaishnavi:वैष्णवी, Manasi:मानसी, Ketaki:केतकी"
        ),
        "S": _pairs(
            "Patil:पाटील, Deshmukh:देशमुख, Kulkarni:कुलकर्णी, Jadhav:जाधव, Pawar:पवार, "
            "Shinde:शिंदे, Joshi:जोशी, More:मोरे, Gaikwad:गायकवाड, Deshpande:देशपांडे, "
            "Chavan:चव्हाण, Bhosale:भोसले, Sawant:सावंत, Naik:नाईक"
        ),
    },
    "MUSLIM": {
        "M": _pairs(
            "Mohammed:मोहम्मद, Imran:इमरान, Faisal:फ़ैसल, Salman:सलमान, Arif:आरिफ़, Zaid:ज़ैद, "
            "Aamir:आमिर, Irfan:इरफ़ान, Danish:दानिश, Sameer:समीर, Shahid:शाहिद, Tariq:तारिक़, "
            "Asif:आसिफ़, Rizwan:रिज़वान, Junaid:जुनैद, Adnan:अदनान"
        ),
        "F": _pairs(
            "Ayesha:आयशा, Fatima:फ़ातिमा, Zainab:ज़ैनब, Sana:सना, Nazia:नाज़िया, "
            "Farheen:फ़रहीन, Shabana:शबाना, Mehreen:मेहरीन, Saba:सबा, Heena:हिना, "
            "Aliya:आलिया, Nida:निदा"
        ),
        "S": _pairs(
            "Khan:ख़ान, Ahmed:अहमद, Ansari:अंसारी, Qureshi:क़ुरैशी, Siddiqui:सिद्दीक़ी, "
            "Shaikh:शेख़, Syed:सैयद, Hussain:हुसैन, Rahman:रहमान, Ali:अली, Mirza:मिर्ज़ा, "
            "Pathan:पठान, Iqbal:इक़बाल"
        ),
    },
    "SOUTH_TN": {
        "M": _pairs(
            "Karthik, Arun, Vignesh, Suresh, Prakash, Senthil, Balaji, Dinesh, Ganesh, Hari, "
            "Manikandan, Rajkumar, Saravanan, Vijay, Ashwin, Gokul"
        ),
        "F": _pairs(
            "Lakshmi, Priya, Divya, Kavya, Deepa, Meena, Revathi, Saranya, Gayathri, Nandhini, "
            "Keerthana, Swathi, Aishwarya, Janani"
        ),
        "S": _pairs(
            "Iyer, Iyengar, Pillai, Murugan, Subramanian, Krishnan, Raman, Natarajan, "
            "Rajendran, Selvam, Kannan, Srinivasan, Venkatesan, Chandran, Raghavan"
        ),
    },
    "SOUTH_KA": {
        "M": _pairs(
            "Kiran, Manjunath, Prashanth, Raghavendra, Santosh, Shivakumar, Naveen, Praveen, "
            "Harish, Sudeep, Chetan, Darshan, Varun, Akshay"
        ),
        "F": _pairs(
            "Sowmya, Shwetha, Pooja, Ramya, Bhavya, Kavya, Spoorthi, Anusha, Deepika, Rashmi, "
            "Sahana, Vidya"
        ),
        "S": _pairs(
            "Gowda, Hegde, Shetty, Rao, Bhat, Kamath, Naik, Patil, Kulkarni, Shenoy, Murthy, "
            "Reddy, Acharya, Prabhu"
        ),
    },
    "SOUTH_TG": {
        "M": _pairs(
            "Srinivas, Venkatesh, Ravi, Suresh, Mahesh, Naresh, Ramesh, Sai, Chaitanya, Krishna, "
            "Vamshi, Harsha, Teja, Pavan, Raju"
        ),
        "F": _pairs(
            "Lakshmi, Swathi, Sravani, Bhavani, Divya, Keerthi, Anusha, Sindhu, Haritha, "
            "Mounika, Sushma, Navya"
        ),
        "S": _pairs(
            "Reddy, Rao, Naidu, Goud, Varma, Chowdary, Yadav, Sharma, Raju, Kumar, Achari, Setty"
        ),
    },
    "EAST": {
        "M": _pairs(
            "Arnab, Sourav, Debashish, Abhijit, Subhankar, Anirban, Rajib, Sayan, Tanmoy, "
            "Pritam, Arijit, Soumya, Indranil, Ritwik"
        ),
        "F": _pairs(
            "Moumita, Priyanka, Sreya, Ankita, Payel, Rima, Tanushree, Sudeshna, Debolina, "
            "Ananya, Puja, Sharmistha"
        ),
        "S": _pairs(
            "Banerjee, Mukherjee, Chatterjee, Das, Ghosh, Bose, Sen, Dutta, Roy, Saha, Mondal, "
            "Chakraborty, Ganguly, Bhattacharya, Sarkar, Paul"
        ),
    },
    "SIKH": {
        "M": _pairs(
            "Gurpreet, Harpreet, Manpreet, Jaspreet, Harjeet, Amandeep, Simranjeet, Navdeep, "
            "Gurmeet, Karan"
        ),
        "F": _pairs("Simran, Harleen, Navneet, Jasleen, Gurleen, Amrit, Kiranjeet, Manpreet"),
        "S": _pairs("Singh, Kaur, Gill, Sandhu, Dhillon, Sidhu, Grewal, Bajwa"),
    },
    "CHRISTIAN": {
        "M": _pairs(
            "Joseph, Thomas, George, John, Abraham, Mathew, Antony, Jacob, Paul, Joel, Samuel, "
            "Daniel"
        ),
        "F": _pairs("Mary, Maria, Ann, Teresa, Elizabeth, Jincy, Sherin, Merin, Riya, Neha"),
        "S": _pairs(
            "Thomas, Joseph, Varghese, Mathew, Fernandes, D'Souza, Pereira, Kurian, Chacko, "
            "Philip, Rodrigues"
        ),
    },
}

# state -> (cities/districts, PIN first digit, region weights)
STATES: dict[str, dict] = {
    "Maharashtra": {
        "cities": ["Pune", "Mumbai", "Nagpur", "Nashik"],
        "pin": "4",
        "regions": {"WEST": 0.65, "NORTH": 0.15, "MUSLIM": 0.12, "SOUTH_KA": 0.08},
    },
    "Karnataka": {
        "cities": ["Bengaluru Urban", "Mysuru", "Mangaluru", "Hubballi"],
        "pin": "5",
        "regions": {
            "SOUTH_KA": 0.65,
            "SOUTH_TG": 0.08,
            "NORTH": 0.10,
            "MUSLIM": 0.12,
            "CHRISTIAN": 0.05,
        },
    },
    "Rajasthan": {
        "cities": ["Jaipur", "Jodhpur", "Udaipur", "Kota"],
        "pin": "3",
        "regions": {"NORTH": 0.85, "MUSLIM": 0.10, "SIKH": 0.05},
    },
    "Tamil Nadu": {
        "cities": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli"],
        "pin": "6",
        "regions": {"SOUTH_TN": 0.80, "CHRISTIAN": 0.08, "MUSLIM": 0.07, "NORTH": 0.05},
    },
    "Uttar Pradesh": {
        "cities": ["Lucknow", "Kanpur Nagar", "Gautam Buddha Nagar", "Varanasi"],
        "pin": "2",
        "regions": {"NORTH": 0.80, "MUSLIM": 0.17, "SIKH": 0.03},
    },
    "Delhi": {
        "cities": ["New Delhi", "South West Delhi", "North West Delhi", "East Delhi"],
        "pin": "1",
        "regions": {
            "NORTH": 0.65,
            "MUSLIM": 0.12,
            "SIKH": 0.08,
            "EAST": 0.05,
            "SOUTH_TN": 0.05,
            "WEST": 0.05,
        },
    },
    "West Bengal": {
        "cities": ["Kolkata", "Paschim Bardhaman", "Darjeeling", "Howrah"],
        "pin": "7",
        "regions": {"EAST": 0.75, "MUSLIM": 0.18, "NORTH": 0.07},
    },
    "Telangana": {
        "cities": ["Hyderabad", "Warangal", "Karimnagar", "Nizamabad"],
        "pin": "5",
        "regions": {"SOUTH_TG": 0.70, "MUSLIM": 0.15, "NORTH": 0.10, "SOUTH_KA": 0.05},
    },
}

LOCALITIES = [
    "MG Road", "Station Road", "Gandhi Nagar", "Shivaji Nagar", "Nehru Colony", "Ram Nagar",
    "Laxmi Nagar", "Indira Colony", "Civil Lines", "Model Town", "Sector 12", "Ashok Nagar",
    "Patel Nagar", "Subhash Nagar", "Kamla Nagar", "Temple Street", "Lake View Road",
]  # fmt: skip

# Foreign nationals: (first, last, gender, nationality)
FOREIGN_NATIONALS = [
    ("Bikash", "Thapa", "M", "Nepal"), ("Sujan", "Shrestha", "M", "Nepal"),
    ("Pratima", "Rai", "F", "Nepal"), ("Sushmita", "Tamang", "F", "Nepal"),
    ("Tanvir", "Hossain", "M", "Bangladesh"), ("Rafiq", "Islam", "M", "Bangladesh"),
    ("Nusrat", "Jahan", "F", "Bangladesh"), ("Farhana", "Akter", "F", "Bangladesh"),
    ("Kasun", "Perera", "M", "Sri Lanka"), ("Dilini", "Silva", "F", "Sri Lanka"),
    ("Farid", "Ahmadzai", "M", "Afghanistan"), ("Mariam", "Sultani", "F", "Afghanistan"),
    ("Chinedu", "Okafor", "M", "Nigeria"), ("Ngozi", "Eze", "F", "Nigeria"),
    ("Brian", "Otieno", "M", "Kenya"), ("Wanjiru", "Kamau", "F", "Kenya"),
    ("Tashi", "Dorji", "M", "Bhutan"), ("Pema", "Wangmo", "F", "Bhutan"),
]  # fmt: skip

OCI_COUNTRIES = ["United States", "United Kingdom", "Canada", "Australia", "Singapore"]
PIO_COUNTRIES = ["Mauritius", "Fiji", "Suriname", "Trinidad and Tobago", "Guyana"]
NRI_COUNTRIES = ["UAE", "Saudi Arabia", "Qatar", "Oman", "Kuwait", "United States", "Singapore"]
NRI_RELATIONS = ["father", "mother", "uncle", "brother"]
